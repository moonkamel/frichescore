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
import statistics
import time
from datetime import date

import httpx

from ..scoring.record import Provenance, Record
from .mapping import row_to_record

# Adresse et paramètres alignés sur le client officiel du Cerema (paquet PyPI « apifoncier2 ») :
# base https://apidf-preprod.cerema.fr, point d'accès /cartofriches/friches (sans « / » final), page_size=500, délai 60 s.
CARTOFRICHES_URL = os.environ.get("CARTOFRICHES_URL", "https://apidf-preprod.cerema.fr/cartofriches/friches")
CARTOFRICHES_PAGE_SIZE = 500
GEORISQUES_URL = os.environ.get("GEORISQUES_URL", "https://www.georisques.gouv.fr/api/v1")
GEODVF_URL = os.environ.get("GEODVF_URL", "https://files.data.gouv.fr/geo-dvf/latest/csv")
TIMEOUT = float(os.environ.get("FRICHESCORE_HTTP_TIMEOUT", "60"))
RETRIES = int(os.environ.get("FRICHESCORE_HTTP_RETRIES", "1"))  # nouvelle tentative après un délai dépassé
MAX_PAGES = 50

# Transport injectable (tests : httpx.MockTransport)
TRANSPORT: httpx.BaseTransport | None = None


class ConnectorError(Exception):
    """Erreur de connecteur au message lisible par un utilisateur non technicien."""

    def __init__(self, message: str, url: str | None = None, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.message, self.url, self.status, self.body = message, url, status, body


def _client(timeout: float | None = None) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout or TIMEOUT, connect=15.0), follow_redirects=True, transport=TRANSPORT,
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


def fetch_cartofriches(code_insee: str, base_url: str | None = None) -> tuple[list[Record], list[str]]:
    rows: list[dict] = []
    url = base_url or CARTOFRICHES_URL
    params: dict | None = {"code_insee": code_insee, "page_size": CARTOFRICHES_PAGE_SIZE}
    with _client() as c:
        for _ in range(MAX_PAGES):
            data, _full, _st = _get(c, url, params)
            rows += parse_cartofriches(data)
            url = data.get("next") if isinstance(data, dict) else None
            params = None
            if not url:
                break
    return cartofriches_records(rows)


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
    return out


def enrich_georisques(rec: Record, rayon_m: int = 150, base_url: str | None = None) -> tuple[Record, list[str]]:
    """Renseigne basias_count, basol, sis, icpe_count (+ régime, SEVESO, statut).
    Chaque base est interrogée indépendamment ; les échecs sont listés."""
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
                url = base_url or CARTOFRICHES_URL
                data, full, st = _get(c, url, {"code_insee": code_insee, "page_size": CARTOFRICHES_PAGE_SIZE})
                rows = parse_cartofriches(data)
                recs, warns = cartofriches_records(rows)
                res.update(url=full, http_status=st, apercu_brut=json.dumps(data, ensure_ascii=False)[:1500],
                           resultat={"friches_page_1": len(rows), "friches_exploitables": len(recs),
                                     "page_suivante": bool(isinstance(data, dict) and data.get("next")),
                                     "exemple": recs[0].values if recs else None, "avertissements": warns[:5]})
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
    except Exception as e:  # noqa: BLE001 — le diagnostic ne doit jamais planter
        res["erreur"] = f"erreur inattendue ({type(e).__name__}) : {str(e)[:300]}"
    res["duree_ms"] = round((time.monotonic() - t0) * 1000)
    return res
