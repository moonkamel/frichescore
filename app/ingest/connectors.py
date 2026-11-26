"""Connecteurs vers les sources publiques ouvertes.

Principes de robustesse :
  * aucune exception réseau ne remonte brute : tout échec devient une
    `ConnectorError` au message lisible (délai dépassé, connexion refusée,
    HTTP 4xx/5xx, réponse non JSON…) ;
  * un échec partiel n'efface rien : si l'API ICPE répond mais pas celle des
    SIS, seul l'indicateur ICPE est renseigné ; l'indicateur SIS reste ABSENT
    (« non interrogé »), jamais mis à False par défaut ;
  * aucune donnée n'est inventée pour combler un échec.

Points d'accès (modifiables par variables d'environnement, et testables depuis
l'onglet Données → « Tester la connexion ») :
  * Cartofriches — API Données foncières du Cerema :
      {CARTOFRICHES_URL}?code_insee=<INSEE>          (pagination via « next »)
  * Géorisques API v1 :
      {GEORISQUES_URL}/ssp/casias        (ex-BASIAS)
      {GEORISQUES_URL}/ssp/instructions  (ex-BASOL)
      {GEORISQUES_URL}/ssp/conclusions_sis
      {GEORISQUES_URL}/installations_classees
    interrogés par latlon=<lon>,<lat>&rayon=<m>
  * DVF géolocalisé (Etalab) :
      {GEODVF_URL}/<année>/communes/<dep>/<insee>.csv
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import socket
import ssl
import statistics
import time
from datetime import date
from urllib.parse import urlparse

import httpx

from ..scoring.record import Provenance, Record
from .mapping import row_to_record

# Adresse et paramètres alignés sur le client officiel du Cerema (paquet PyPI « apifoncier2 ») :
# base https://apidf-preprod.cerema.fr, page_size=500, délai 60 s. Le point d'accès /cartofriches/friches ne
# renvoie pas de géométrie ; /cartofriches/geofriches renvoie le même contenu en GeoJSON, avec le contour du site.
# FricheScore interroge les DEUX et les fusionne par identifiant : attributs complets (/friches) + position (/geofriches).
CARTOFRICHES_URL = os.environ.get("CARTOFRICHES_URL", "https://apidf-preprod.cerema.fr/cartofriches")


def cartofriches_urls(base: str | None = None) -> tuple[str, str]:
    """Adresses (attributs, géométrie) à partir de la base ou de l'un des deux points d'accès."""
    b = (base or CARTOFRICHES_URL).rstrip("/")
    for suffix in ("/geofriches", "/friches"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
    return b + "/friches", b + "/geofriches"
CARTOFRICHES_PAGE_SIZE = 500
GEORISQUES_URL = os.environ.get("GEORISQUES_URL", "https://www.georisques.gouv.fr/api/v1")
GEODVF_URL = os.environ.get("GEODVF_URL", "https://files.data.gouv.fr/geo-dvf/latest/csv")
TIMEOUT = float(os.environ.get("FRICHESCORE_HTTP_TIMEOUT", "60"))
RETRIES = int(os.environ.get("FRICHESCORE_HTTP_RETRIES", "1"))  # nouvelle tentative après un délai dépassé
MAX_PAGES = 50

# Forcer IPv4 : dans Docker Desktop (Windows/macOS), l'IPv6 sortant est souvent non routé ; sans
# repli automatique, la connexion attend alors jusqu'au délai maximal. Désactivable : FRICHESCORE_FORCE_IPV4=0.
FORCE_IPV4 = os.environ.get("FRICHESCORE_FORCE_IPV4", "1").lower() not in ("0", "false", "non", "no")

# Message d'ouverture TLS compact : OpenSSL ≥ 3.5 (image python:3.12-slim) propose par défaut un échange de clés
# post-quantique qui porte le ClientHello à ~1 500 octets. Certains réseaux (Docker Desktop/WSL2, VPN, pare-feux)
# perdent ce paquet sans réponse : la connexion TCP réussit mais la négociation TLS reste bloquée. En limitant
# l'échange de clés à la courbe standard P-256, le ClientHello retombe à ~500 octets (toujours en TLS 1.3).
# Désactivable : FRICHESCORE_TLS_COMPACT=0.
TLS_COMPACT = os.environ.get("FRICHESCORE_TLS_COMPACT", "1").lower() not in ("0", "false", "non", "no")


def ssl_context(compact: bool | None = None) -> ssl.SSLContext:
    """Contexte TLS vérifiant les certificats (certifi, ou SSL_CERT_FILE pour une autorité d'entreprise)."""
    cafile = os.environ.get("SSL_CERT_FILE")
    if not cafile:
        try:
            import certifi
            cafile = certifi.where()
        except ImportError:
            cafile = None
    ctx = ssl.create_default_context(cafile=cafile)
    if TLS_COMPACT if compact is None else compact:
        ctx.set_ecdh_curve("prime256v1")
    return ctx


# Transport injectable (tests : httpx.MockTransport)
TRANSPORT: httpx.BaseTransport | None = None


class ConnectorError(Exception):
    """Erreur de connecteur au message lisible par un utilisateur non technicien."""

    def __init__(self, message: str, url: str | None = None, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.message, self.url, self.status, self.body = message, url, status, body


def _transport() -> httpx.BaseTransport:
    if TRANSPORT is not None:
        return TRANSPORT
    # lier une adresse IPv4 locale = connexions IPv4 uniquement
    return httpx.HTTPTransport(verify=ssl_context(), local_address="0.0.0.0" if FORCE_IPV4 else None)


def tls_probe(host: str, ip: str, port: int = 443, compact: bool = False, timeout: float = 8.0) -> dict:
    """Tente une négociation TLS complète vers une adresse donnée."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            with ssl_context(compact).wrap_socket(sock, server_hostname=host) as t:
                v = t.version()
        return {"ok": True, "version": v, "duree_ms": round((time.monotonic() - t0) * 1000), "erreur": None}
    except (OSError, ssl.SSLError) as e:
        return {"ok": False, "version": None, "duree_ms": round((time.monotonic() - t0) * 1000),
                "erreur": ("délai dépassé" if isinstance(e, (socket.timeout, TimeoutError)) else str(e))[:140]}


def proxy_env() -> dict:
    out = {}
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        v = os.environ.get(k)
        if v:
            out[k] = v.split("@")[-1] if "@" in v else v  # ne jamais afficher d'identifiants
    return out


def network_diagnostic(url: str, port: int = 443, timeout: float = 5.0) -> dict:
    """Diagnostic réseau depuis la machine (ou le conteneur) qui exécute FricheScore :
    résolution DNS, puis tentative de connexion TCP sur chaque adresse IPv4 et IPv6."""
    host = urlparse(url).hostname or url
    diag = {"hote": host, "ipv4_force": FORCE_IPV4, "tls_compact": TLS_COMPACT, "openssl": ssl.OPENSSL_VERSION,
            "proxy": proxy_env(), "adresses": [], "tls": None, "conclusion": ""}
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        diag["conclusion"] = (f"le nom « {host} » ne se résout pas (DNS) : {e}. Sous Docker, vérifier la configuration DNS "
                              "de Docker Desktop ou le VPN ; sinon, l'adresse de l'API est peut-être erronée.")
        return diag
    seen = set()
    for fam, _t, _p, _c, sa in infos:
        ip = sa[0]
        if ip in seen:
            continue
        seen.add(ip)
        t0 = time.monotonic()
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                ok, err = True, None
        except OSError as e:
            ok, err = False, (str(e) or type(e).__name__)[:120]
        diag["adresses"].append({"ip": ip, "famille": "IPv6" if fam == socket.AF_INET6 else "IPv4", "ok": ok,
                                 "duree_ms": round((time.monotonic() - t0) * 1000), "erreur": err})
    v4 = [a for a in diag["adresses"] if a["famille"] == "IPv4"]
    v6 = [a for a in diag["adresses"] if a["famille"] == "IPv6"]
    reach = next((a for a in v4 if a["ok"]), None) or next((a for a in v6 if a["ok"]), None)
    if reach and port == 443:
        std = tls_probe(host, reach["ip"], port, compact=False)
        cmp_ = tls_probe(host, reach["ip"], port, compact=True)
        diag["tls"] = {"ip": reach["ip"], "standard": std, "compact": cmp_}
        if cmp_["ok"] and not std["ok"]:
            diag["conclusion"] = ("la connexion TCP réussit mais la négociation TLS standard (message d'ouverture ~1 500 octets) "
                                  "reste sans réponse, alors que la négociation compacte aboutit : le réseau perd les gros paquets "
                                  "(MTU Docker/VPN ou pare-feu). Garder FRICHESCORE_TLS_COMPACT=1 (réglage par défaut)."
                                  + ("" if TLS_COMPACT else " ATTENTION : le mode compact est actuellement désactivé."))
            return diag
        if not cmp_["ok"] and not std["ok"]:
            diag["conclusion"] = ("la connexion TCP réussit mais aucune négociation TLS n'aboutit : un équipement réseau "
                                  "(pare-feu, inspection TLS, antivirus) bloque le chiffrement depuis cette machine. Si une "
                                  "autorité de certification d'entreprise est utilisée, renseigner SSL_CERT_FILE ; sinon, lancer "
                                  "FricheScore sans Docker.")
            return diag
    if any(a["ok"] for a in v4):
        diag["conclusion"] = ("le serveur est joignable en IPv4 depuis cette machine"
                              + (" mais pas en IPv6 : garder l'option IPv4 forcée (réglage par défaut)." if v6 and not any(a["ok"] for a in v6) else ".")
                              + (" Un proxy est configuré : s'il bloque ce domaine, l'appel HTTPS échouera malgré tout."
                                 if any(k.lower() != "no_proxy" for k in diag["proxy"]) else ""))
    elif any(a["ok"] for a in v6):
        diag["conclusion"] = "le serveur n'est joignable qu'en IPv6 : désactiver l'IPv4 forcée (FRICHESCORE_FORCE_IPV4=0)."
    else:
        diag["conclusion"] = ("aucune adresse du serveur n'est joignable depuis cette machine. Si FricheScore tourne dans Docker "
                              "alors que le navigateur ou PowerShell accèdent au site, le PC passe sans doute par un proxy ou un VPN "
                              "que le conteneur ne connaît pas : renseigner HTTPS_PROXY avant « docker compose up », ou lancer "
                              "FricheScore sans Docker." + ("" if any(k.lower() != "no_proxy" for k in diag["proxy"]) else " Aucun proxy n'est configuré pour FricheScore."))
    return diag


def _client(timeout: float | None = None) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout or TIMEOUT, connect=15.0), follow_redirects=True, transport=_transport(),
                        headers={"User-Agent": "FricheScore/0.2 (outil local)", "Accept": "application/json, text/csv, */*"})


def _get(c: httpx.Client, url: str, params: dict | None = None, expect: str = "json"):
    """GET avec traduction des erreurs et une nouvelle tentative après un délai dépassé.
    expect = 'json' | 'text'."""
    r = None
    for attempt in range(RETRIES + 1):
        try:
            r = c.get(url, params=params)
            break
        except httpx.ConnectTimeout:
            raise ConnectorError("connexion impossible dans le délai : le serveur ne répond pas (service arrêté, "
                                 "adresse erronée ou réseau filtrant)", url)
        except httpx.TimeoutException:
            if attempt < RETRIES:
                time.sleep(2)
                continue
            t = c.timeout.read or TIMEOUT
            raise ConnectorError(f"délai dépassé ({t:.0f} s, {RETRIES + 1} tentative(s)) : le serveur accepte la connexion "
                                 "mais ne renvoie pas de réponse — service surchargé ou en maintenance ; réessayer plus tard "
                                 "ou augmenter le délai", url)
        except httpx.ProxyError as e:
            raise ConnectorError("le proxy de sortie de cette machine refuse l'accès à ce service — autoriser l'adresse "
                                 "dans le proxy/pare-feu ou lancer FricheScore depuis un poste ayant accès à Internet : "
                                 + str(e)[:160], url)
        except httpx.ConnectError as e:
            raise ConnectorError("connexion impossible (service hors ligne, DNS, proxy ou pare-feu) : " + str(e)[:160], url)
        except httpx.HTTPError as e:
            raise ConnectorError(f"erreur réseau ({type(e).__name__}) : {str(e)[:160]}", url)
    full = str(r.request.url) if r.request else url
    if r.status_code == 404:
        raise ConnectorError("ressource introuvable (HTTP 404) — vérifier l'adresse de l'API ou le code demandé", full, 404, r.text[:500])
    if r.status_code in (401, 403):
        raise ConnectorError(f"accès refusé (HTTP {r.status_code}) — l'API exige peut-être une clé ou bloque cette adresse", full, r.status_code, r.text[:500])
    if r.status_code == 429:
        raise ConnectorError("trop de requêtes (HTTP 429) — réessayer dans quelques minutes", full, 429, r.text[:500])
    if r.status_code >= 400:
        raise ConnectorError(f"le service a renvoyé une erreur HTTP {r.status_code}", full, r.status_code, r.text[:500])
    if expect == "text":
        return r.text, full, r.status_code
    try:
        return r.json(), full, r.status_code
    except (json.JSONDecodeError, ValueError):
        raise ConnectorError("réponse illisible : JSON attendu (l'API a peut-être changé de format)", full, r.status_code, r.text[:500])


# ------------------------------------------------------------- Cartofriches
def parse_cartofriches(payload) -> list[dict]:
    """Accepte une page paginée ({results: [...]}), du GeoJSON ({features: [...]}) ou une liste."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        raise ConnectorError("format Cartofriches inattendu : objet ou liste JSON attendu")
    if "features" in payload:
        out = []
        for f in payload.get("features") or []:
            props = dict(f.get("properties") or {})
            if f.get("id") is not None:  # GeoJSON : l'identifiant est porté par l'objet, pas par ses propriétés
                props.setdefault("site_id", f.get("id"))
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates")
            if geom.get("type") == "Point" and coords:
                props.setdefault("longitude", coords[0])
                props.setdefault("latitude", coords[1])
            elif geom.get("type") in ("Polygon", "MultiPolygon") and coords:
                pts = _flatten(coords)
                if pts:
                    props.setdefault("longitude", sum(p[0] for p in pts) / len(pts))
                    props.setdefault("latitude", sum(p[1] for p in pts) / len(pts))
            out.append(props)
        return out
    res = payload.get("results")
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and "features" in res:
        return parse_cartofriches(res)
    raise ConnectorError("format Cartofriches inattendu : ni « results », ni « features »")


def _flatten(coords):
    if coords and isinstance(coords[0], (int, float)):
        return [coords]
    out = []
    for c in coords:
        out += _flatten(c)
    return out


def cartofriches_records(rows: list[dict]) -> tuple[list[Record], list[str]]:
    out, warnings = [], []
    today = date.today().isoformat()
    for i, row in enumerate(rows, 1):
        rec, _ = row_to_record(row, "API Cartofriches (Cerema)", today)
        if not rec.has("id"):
            warnings.append(f"friche n°{i} ignorée : identifiant absent")
            continue
        rec.values["id"] = f"CF-{rec.get('id')}"
        _derive_cartofriches(rec, row, today)
        for src_key, key in (("l_basol", "basol"), ("l_basias", "basias_count"), ("l_icpe", "icpe_count")):
            if src_key in row:
                v = row.get(src_key)
                items = [x for x in (v if isinstance(v, list) else str(v or "").replace(";", "|").replace(",", "|").split("|"))
                         if str(x).strip()]
                val = bool(items) if key == "basol" else len(items)
                rec.set(key, val, Provenance(f"{key.split('_')[0].upper()} via Cartofriches", today,
                                             "faible" if key == "basias_count" else "eleve"))
        out.append(rec)
    return out, warnings


def _fetch_pages(c: httpx.Client, url: str, params: dict) -> tuple[list[dict], str, int]:
    rows: list[dict] = []
    full, st = url, 0
    for _ in range(MAX_PAGES):
        data, full_, st = _get(c, url, params)
        full = full if rows else full_
        rows += parse_cartofriches(data)
        url = data.get("next") if isinstance(data, dict) else None
        params = None
        if not url:
            break
    return rows, full, st


def _row_id(r: dict):
    v = r.get("site_id") or r.get("id")
    return str(v) if v not in (None, "") else None


def _row_key(r: dict):
    try:
        surf = round(float(r.get("site_surface") or r.get("unite_fonciere_surface") or 0))
    except (TypeError, ValueError):
        surf = 0
    return (str(r.get("site_nom") or "").strip().lower(), str(r.get("comm_insee") or ""), surf)


def merge_cartofriches(attr_rows: list[dict], geo_rows: list[dict]) -> list[dict]:
    """Fusionne attributs (/friches) et géométrie (/geofriches) : par identifiant de site, à défaut par
    (nom, commune, surface). Ne crée jamais de doublon : un site de /geofriches non apparié n'est ajouté
    que s'il n'existe pas d'équivalent côté attributs."""
    if not attr_rows:
        return list(geo_rows)
    if not geo_rows:
        return list(attr_rows)
    by_id = {_row_id(g): g for g in geo_rows if _row_id(g)}
    by_key: dict = {}
    for g in geo_rows:
        by_key.setdefault(_row_key(g), []).append(g)
    used = set()
    out = []
    for r in attr_rows:
        g = by_id.get(_row_id(r))
        if g is None or id(g) in used:
            cands = [x for x in by_key.get(_row_key(r), []) if id(x) not in used]
            g = cands[0] if cands else None
        if g is not None:
            used.add(id(g))
            r = {**{k: v for k, v in g.items() if v not in (None, "")}, **{k: v for k, v in r.items() if v not in (None, "")}}
        out.append(r)
    attr_ids = {_row_id(r) for r in attr_rows}
    attr_keys = {_row_key(r) for r in attr_rows}
    out += [g for g in geo_rows if id(g) not in used and _row_id(g) not in attr_ids and _row_key(g) not in attr_keys]
    return out


def _derive_cartofriches(rec: Record, row: dict, today: str) -> None:
    """Exploite les champs propres à Cartofriches observés sur l'API réelle."""
    src = "API Cartofriches (Cerema)"
    refcad = row.get("unite_fonciere_refcad")
    if refcad:
        if isinstance(refcad, str):
            refcad = [x for x in re.split(r"[\[\]',;| ]+", refcad) if x]
        if isinstance(refcad, list) and refcad:
            rec.set("nb_parcelles", len(refcad), Provenance(f"{src} — références cadastrales", today, "eleve"))
    statut = str(row.get("site_statut") or "").lower()
    if "avec projet" in statut:
        rec.set("projet_connu", True, Provenance(src, today, "moyen", "statut « friche avec projet »"))
    elif "sans projet" in statut:
        rec.set("projet_connu", False, Provenance(src, today, "moyen", "statut « friche sans projet »"))
    sn0 = str(row.get("source_nom") or "").lower()
    if "fonds friches" in sn0 and not rec.has("projet_connu"):
        rec.set("projet_connu", True, Provenance(src, today, "moyen", "lauréat de l'appel à projets Fonds Friches"))
    origine = " — ".join(str(row.get(k)) for k in ("source_nom", "nature") if row.get(k))
    if origine:
        rec.set("source_inventaire", origine, Provenance(src, today, "moyen"))
    sn = str(row.get("source_nom") or "").lower()
    if "basias" in sn or "basol" in sn:
        # Signal d'usage passé potentiellement polluant : rend la friche évaluable, sans préjuger du résultat
        # Géorisques (qui renseigne ensuite basol / basias_count / icpe_count avec leur propre provenance).
        verif = "vérifié" in sn and "non vérifié" not in sn
        if not rec.has("activite_passee"):
            rec.set("activite_passee", "ancien site BASIAS/BASOL (repéré par Cartofriches)",
                    Provenance(src, today, "moyen" if verif else "faible", row.get("source_nom")))
    nature = str(row.get("nature") or "").lower()
    if "non vérifi" in nature and rec.has("statut_cartofriches") and rec.get("statut_cartofriches") == "verifiee":
        rec.set("statut_cartofriches", "non_verifiee", Provenance(src, today, "moyen", f"nature : {row.get('nature')}"))


def fetch_cartofriches(code_insee: str, base_url: str | None = None) -> tuple[list[Record], list[str]]:
    """Attributs + géométrie, fusionnés. Si l'un des deux points d'accès échoue, l'autre suffit (avec avertissement)."""
    u_attr, u_geo = cartofriches_urls(base_url)
    params = {"code_insee": code_insee, "page_size": CARTOFRICHES_PAGE_SIZE}
    warns: list[str] = []
    attr: list[dict] = []
    geo: list[dict] = []
    errors = []
    with _client() as c:
        try:
            attr, _f, _s = _fetch_pages(c, u_attr, dict(params))
        except ConnectorError as e:
            errors.append(e)
            warns.append(f"attributs Cartofriches indisponibles ({e.message}) : seuls les champs de /geofriches sont importés")
        try:
            geo, _f, _s = _fetch_pages(c, u_geo, dict(params))
        except ConnectorError as e:
            errors.append(e)
            warns.append(f"géométrie Cartofriches indisponible ({e.message}) : friches importées sans coordonnées")
    if len(errors) == 2:
        raise errors[0]
    recs, w2 = cartofriches_records(merge_cartofriches(attr, geo))
    return recs, warns + w2


def field_report(rows: list[dict]) -> dict:
    """Inventaire des champs réellement renvoyés : sert à caler les correspondances sur les vraies données."""
    from .mapping import resolve_column
    if not rows:
        return {}
    cols: dict[str, list] = {}
    for r in rows:
        for k, v in r.items():
            cols.setdefault(k, []).append(v)
    first = {k: (str(v)[:120] if v is not None else None) for k, v in rows[0].items()}
    recon, non_recon, distinct = {}, [], {}
    for k, vals in cols.items():
        key = resolve_column(k)
        if key:
            recon[k] = key
        else:
            non_recon.append(k)
        txt = [v for v in vals if isinstance(v, str) and v.strip()]
        uniq = sorted(set(txt))
        if txt and len(uniq) <= 25 and len(uniq) < len(vals):
            distinct[k] = uniq
        cols[k] = sum(1 for v in vals if v not in (None, "", []))
    return {"premiere_friche_brute": first, "colonnes_reconnues": recon, "colonnes_non_reconnues": sorted(non_recon),
            "taux_remplissage": {k: f"{n}/{len(rows)}" for k, n in cols.items()}, "valeurs_distinctes": distinct}


# --------------------------------------------------------------- Géorisques
GEO_ENDPOINTS = {
    "casias": "ssp/casias",
    "instructions": "ssp/instructions",
    "sis": "ssp/conclusions_sis",
    "icpe": "installations_classees",
}


def georisques_count(payload) -> int:
    """Nombre de résultats d'une réponse Géorisques v1 ({results: n, data: [...]})."""
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        raise ConnectorError("format Géorisques inattendu")
    if isinstance(payload.get("results"), int):
        return payload["results"]
    data = payload.get("data")
    if isinstance(data, list):
        return len(data)
    raise ConnectorError("format Géorisques inattendu : ni « results » ni « data »")


def _icpe_fields(items: list[dict], today: str) -> dict:
    out = {}
    if not items:
        return out
    it = items[0]
    reg = str(it.get("regime") or it.get("regimeVigueur") or "").lower()
    if reg:
        out["icpe_regime"] = "autorisation" if "autoris" in reg else "enregistrement" if "enregis" in reg else "declaration"
    sev = str(it.get("statutSeveso") or "").lower()
    if "haut" in sev:
        out["seveso"] = "haut"
    elif "bas" in sev:
        out["seveso"] = "bas"
    etat = str(it.get("etatActivite") or it.get("etat_activite") or "").lower()
    if etat:
        out["icpe_statut"] = "cessee" if ("cess" in etat or "arr" in etat) else "en_activite"
    sirens = sorted({siren_of(x.get("siret") or x.get("numeroSiret") or x.get("siren")) for x in items} - {None})
    if sirens:  # identifiants d'entreprises seulement (pas de raison sociale : elle peut être un nom de personne)
        out["siren_exploitants"] = sirens[:10]
    return out


def siren_of(v) -> str | None:
    """SIREN (9 chiffres) extrait d'un SIRET ou d'un SIREN, sinon None."""
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d[:9] if len(d) in (9, 14) else None


def search_radius(surface_m2) -> int:
    """Rayon de recherche adapté à la taille du site : rayon du disque de même surface + 25 m de marge
    (imprécision du géocodage des anciens sites), borné entre 30 et 400 m. Un rayon fixe large
    attribuerait à une petite parcelle urbaine tous les anciens sites industriels du quartier."""
    import math
    try:
        s_ = float(surface_m2 or 0)
    except (TypeError, ValueError):
        s_ = 0.0
    return int(max(30, min(400, round(math.sqrt(max(s_, 0) / math.pi) + 25))))


def enrich_georisques(rec: Record, rayon_m: int | None = None, base_url: str | None = None) -> tuple[Record, list[str]]:
    """Renseigne basias_count, basol, sis, icpe_count (+ régime, SEVESO, statut).
    Chaque base est interrogée indépendamment ; les échecs sont listés."""
    if rayon_m is None:
        rayon_m = search_radius(rec.get("surface_m2"))
    if not (rec.has("lat") and rec.has("lon")):
        return rec, ["Géorisques : coordonnées manquantes, bases de pollution non interrogées"]
    base = (base_url or GEORISQUES_URL).rstrip("/")
    ll = f"{rec.get('lon')},{rec.get('lat')}"
    today = date.today().isoformat()
    errors = []
    meth = f"recherche dans un rayon de {rayon_m} m"
    with _client() as c:
        def q(name):
            try:
                data, _f, _s = _get(c, f"{base}/{GEO_ENDPOINTS[name]}", {"latlon": ll, "rayon": rayon_m})
                return data
            except ConnectorError as e:
                errors.append(f"Géorisques {name} : {e.message}")
                return None
        d = q("casias")
        if d is not None:
            try:
                rec.set("basias_count", georisques_count(d), Provenance("CASIAS (ex-BASIAS) / Géorisques", today, "faible", meth))
            except ConnectorError as e:
                errors.append(f"Géorisques casias : {e.message}")
        d = q("instructions")
        if d is not None:
            try:
                rec.set("basol", georisques_count(d) > 0, Provenance("Instructions SSP (ex-BASOL) / Géorisques", today, "eleve", meth))
            except ConnectorError as e:
                errors.append(f"Géorisques instructions : {e.message}")
        d = q("sis")
        if d is not None:
            try:
                rec.set("sis", georisques_count(d) > 0, Provenance("SIS / Géorisques", today, "eleve", meth))
            except ConnectorError as e:
                errors.append(f"Géorisques sis : {e.message}")
        d = q("icpe")
        if d is not None:
            try:
                n = georisques_count(d)
                rec.set("icpe_count", n, Provenance("ICPE / Géorisques", today, "eleve", meth))
                items = d.get("data") if isinstance(d, dict) else d
                for k, v in _icpe_fields(items if isinstance(items, list) else [], today).items():
                    rec.set(k, v, Provenance("ICPE / Géorisques", today, "eleve"))
            except ConnectorError as e:
                errors.append(f"Géorisques icpe : {e.message}")
    return rec, errors


# ---------------------------------------------------------------------- DVF
def dvf_indicators(csv_texts: dict[int, str]) -> dict:
    """Indicateurs de marché à partir des fichiers geo-dvf d'une commune
    ({année: contenu CSV}). Ventes d'appartements et de maisons, prix/m² bâti."""
    by_year: dict[int, list[float]] = {}
    for year, text in csv_texts.items():
        seen = set()
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "valeur_fonciere" not in reader.fieldnames:
            raise ConnectorError(f"fichier DVF {year} illisible : colonne « valeur_fonciere » absente")
        for row in reader:
            if row.get("nature_mutation") != "Vente" or row.get("type_local") not in ("Appartement", "Maison"):
                continue
            mid = row.get("id_mutation")
            if mid in seen:
                continue
            seen.add(mid)
            try:
                v = float(row["valeur_fonciere"])
                s = float(row["surface_reelle_bati"])
            except (KeyError, ValueError, TypeError):
                continue
            if s < 15 or v < 10_000:
                continue
            p = v / s
            if 300 < p < 25_000:
                by_year.setdefault(year, []).append(p)
    if not by_year:
        return {}
    years = sorted(by_year)
    last = statistics.median(by_year[years[-1]])
    out = {"prix_m2_logement": round(last), "nb_mutations_3ans": sum(len(by_year[y]) for y in years[-3:]),
           "_annees": years}
    if len(years) >= 2:
        first = statistics.median(by_year[years[0]])
        out["evol_prix_3ans_pct"] = round((last / first - 1) * 100, 1)
    return out


def fetch_dvf(code_insee: str, years: list[int], base_url: str | None = None) -> tuple[dict, list[str]]:
    dep = code_insee[:3] if code_insee.startswith("97") else code_insee[:2]
    base = (base_url or GEODVF_URL).rstrip("/")
    texts, errors = {}, []
    with _client() as c:
        for y in years:
            try:
                txt, _f, _s = _get(c, f"{base}/{y}/communes/{dep}/{code_insee}.csv", expect="text")
                texts[y] = txt
            except ConnectorError as e:
                errors.append(f"DVF {y} : {e.message}")
    try:
        return dvf_indicators(texts), errors
    except ConnectorError as e:
        return {}, errors + [e.message]


def dvf_commune_record(code_insee: str, ind: dict) -> Record:
    r = Record()
    an = max(ind.get("_annees") or [date.today().year])
    p = Provenance("DVF géolocalisé (Etalab)", str(an), "eleve", f"médiane appartements+maisons, années {ind.get('_annees')}")
    for k in ("prix_m2_logement", "nb_mutations_3ans", "evol_prix_3ans_pct"):
        if k in ind:
            r.set(k, ind[k], p)
    r.values["code_insee"] = code_insee
    return r


# ---------------------------------------------------------- test de connexion
def test_connection(source: str, code_insee: str = "59350", lat: float = 50.6329, lon: float = 3.0586,
                    base_url: str | None = None, timeout: float | None = None) -> dict:
    """Appel RÉEL et diagnostic lisible, pour l'onglet Données."""
    t0 = time.monotonic()
    res = {"source": source, "ok": False, "url": None, "http_status": None, "duree_ms": None,
           "apercu_brut": None, "resultat": None, "erreur": None}
    try:
        with _client(timeout) as c:
            if source == "cartofriches":
                u_attr, u_geo = cartofriches_urls(base_url)
                params = {"code_insee": code_insee, "page_size": CARTOFRICHES_PAGE_SIZE}
                parts, errs, raw = {}, {}, {}
                attr, geo, last = [], [], (None, None)
                for name, u in (("friches", u_attr), ("geofriches", u_geo)):
                    try:
                        data, full, st = _get(c, u, dict(params))
                        rows = parse_cartofriches(data)
                        parts[name] = {"url": full, "http": st, "friches": len(rows),
                                       "page_suivante": bool(isinstance(data, dict) and data.get("next"))}
                        raw[name] = json.dumps(data, ensure_ascii=False)[:1500]
                        last = (full, st)
                        if name == "friches":
                            attr = rows
                        else:
                            geo = rows
                    except ConnectorError as e:
                        errs[name] = e.message + (f" [{e.url}]" if e.url else "")
                if not parts:
                    raise ConnectorError("aucun des deux points d'accès Cartofriches n'a répondu : " + " ; ".join(errs.values()))
                merged = merge_cartofriches(attr, geo)
                recs, warns = cartofriches_records(merged)
                res.update(url=last[0], http_status=last[1], apercu_brut=json.dumps(raw, ensure_ascii=False)[:3000],
                           resultat={"points_d_acces": parts, "erreurs": errs, "friches_fusionnees": len(merged),
                                     "friches_exploitables": len(recs),
                                     "avec_coordonnees": sum(1 for r in recs if r.has("lat") and r.has("lon")),
                                     "avec_surface": sum(1 for r in recs if r.has("surface_m2")),
                                     "exemple_interprete": recs[0].values if recs else None, "avertissements": warns[:5]},
                           champs=field_report(merged))
            elif source == "georisques":
                base = (base_url or GEORISQUES_URL).rstrip("/")
                counts, errs, raw, last = {}, {}, {}, (None, None)
                for name, path in GEO_ENDPOINTS.items():
                    try:
                        data, full, st = _get(c, f"{base}/{path}", {"latlon": f"{lon},{lat}", "rayon": 500})
                        counts[name] = georisques_count(data)
                        raw[name] = json.dumps(data, ensure_ascii=False)[:400]
                        last = (full, st)
                    except ConnectorError as e:
                        errs[name] = e.message + (f" [{e.url}]" if e.url else "")
                res.update(url=last[0], http_status=last[1], apercu_brut=json.dumps(raw, ensure_ascii=False)[:1500],
                           resultat={"resultats_par_base (rayon 500 m)": counts, "erreurs": errs})
                if not counts:
                    raise ConnectorError("aucune des 4 bases Géorisques n'a répondu : " + " ; ".join(errs.values()))
            elif source == "dvf":
                y = date.today().year - 2
                base = (base_url or GEODVF_URL).rstrip("/")
                dep = code_insee[:3] if code_insee.startswith("97") else code_insee[:2]
                txt, full, st = _get(c, f"{base}/{y}/communes/{dep}/{code_insee}.csv", expect="text")
                ind = dvf_indicators({y: txt})
                res.update(url=full, http_status=st, apercu_brut=txt[:1500],
                           resultat={"annee": y, "lignes": max(0, txt.count("\n") - 1), "indicateurs": ind})
            else:
                raise ConnectorError(f"source inconnue : {source}")
        res["ok"] = True
    except ConnectorError as e:
        res.update(erreur=e.message, url=res["url"] or e.url, http_status=res["http_status"] or e.status,
                   apercu_brut=res["apercu_brut"] or e.body)
        if e.status is None and TRANSPORT is None:  # échec réseau (pas une erreur HTTP) : diagnostic automatique
            target = e.url or base_url or {"cartofriches": cartofriches_urls()[0], "georisques": GEORISQUES_URL}.get(source, GEODVF_URL)
            try:
                res["diagnostic_reseau"] = network_diagnostic(target)
            except Exception as ex:  # noqa: BLE001
                res["diagnostic_reseau"] = {"conclusion": f"diagnostic impossible : {ex}"}
    except Exception as e:  # noqa: BLE001 — le diagnostic ne doit jamais planter
        res["erreur"] = f"erreur inattendue ({type(e).__name__}) : {str(e)[:300]}"
    res["duree_ms"] = round((time.monotonic() - t0) * 1000)
    return res
