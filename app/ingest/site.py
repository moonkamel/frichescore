"""Indicateurs propres à chaque site, calculés à partir des coordonnées de la friche.

  * gare la plus proche — portail open data SNCF (export CSV des gares de voyageurs), repli OpenStreetMap ;
  * desserte en transports collectifs — arrêts OpenStreetMap via l'API Overpass, UNE requête par commune
    (le fichier national d'arrêts de transport.data.gouv.fr pèse ~417 Mo et n'est pas dédoublonné) ;
  * ensoleillement — API PVGIS 5.3 (Commission européenne), mutualisé par carreau de 0,1° ;
  * risques naturels — rapport de risques Géorisques à l'adresse (1 appel/s), repli sur la liste
    GASPAR des risques de la commune (moins précise, confiance moyenne).

Même logique que les autres connecteurs : un échec n'empêche pas les autres sources et n'invente rien ;
la détection des champs est tolérante et exposée par le test de connexion.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import time
from datetime import date
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from ..scoring.record import Provenance
from . import connectors as K
from .territoire import _norm, _num, cache_dir, distance_km

SNCF_EXPORT = os.environ.get("SNCF_GARES_URL",
                             "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/{ds}/exports/csv")
SNCF_DATASETS = [d.strip() for d in os.environ.get("SNCF_GARES_DATASETS", "gares-de-voyageurs,referentiel-gares-voyageurs,liste-des-gares").split(",")]
OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
# Serveurs Overpass publics de secours (le serveur principal renvoie parfois 502/504 en cas de surcharge)
OVERPASS_SECOURS = [u for u in os.environ.get("OVERPASS_SECOURS", "https://overpass.private.coffee/api/interpreter,"
                                              "https://overpass.kumi.systems/api/interpreter").split(",") if u]
PVGIS_URL = os.environ.get("PVGIS_URL", "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc")
GEORISQUES_URL = os.environ.get("GEORISQUES_URL", "https://www.georisques.gouv.fr/api/v1")
APICARTO_URL = os.environ.get("APICARTO_URL", "https://apicarto.ign.fr/api")
GEOPF_WFS_URL = os.environ.get("GEOPF_WFS_URL", "https://data.geopf.fr/wfs/ows")
OSM = "© contributeurs OpenStreetMap (ODbL) via Overpass"


# ------------------------------------------------------------------- gares
def _latlon_from_row(r: dict) -> tuple[float, float] | None:
    """Coordonnées d'une ligne d'export : colonne géographique « lat, lon », colonnes séparées, ou JSON."""
    for k, v in r.items():
        nk = _norm(k)
        if v and ("geo" in nk or "position" in nk or "coord" in nk) and isinstance(v, str):
            nums = re.findall(r"-?\d+(?:\.\d+)?", v)
            if v.strip().startswith("{"):
                try:
                    j = json.loads(v)
                    if "lat" in j and ("lon" in j or "lng" in j):
                        return float(j["lat"]), float(j.get("lon", j.get("lng")))
                    if "coordinates" in j:
                        return float(j["coordinates"][1]), float(j["coordinates"][0])
                except (ValueError, KeyError, TypeError):
                    pass
            if len(nums) >= 2:
                a, b = float(nums[0]), float(nums[1])
                if 41 <= a <= 52 and -6 <= b <= 10:
                    return a, b
                if 41 <= b <= 52 and -6 <= a <= 10:
                    return b, a
    lat = next((v for k, v in r.items() if re.fullmatch(r"(lat(itude)?|y_?wgs84|y_wgs_84)", _norm(k))), None)
    lon = next((v for k, v in r.items() if re.fullmatch(r"(lon(gitude)?|lng|x_?wgs84|x_wgs_84)", _norm(k))), None)
    la, lo = _num(lat), _num(lon)
    if la is not None and lo is not None and 41 <= la <= 52 and -6 <= lo <= 10:
        return la, lo
    return None


def _is_passenger(r: dict) -> bool:
    v = next((v for k, v in r.items() if _norm(k) in ("voyageurs", "voyageur", "est_voyageur")), None)
    return v is None or str(v).strip().upper() in ("O", "OUI", "TRUE", "1", "Y")


def load_stations(refresh: bool = False) -> tuple[list[dict], dict]:
    """Gares de voyageurs SNCF (cache 90 jours). Retourne ([{nom, lat, lon}], métadonnées)."""
    d = cache_dir()
    meta_p, data_p = d / "gares.json", d / "gares_points.json"
    if not refresh and meta_p.exists() and data_p.exists():
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        if time.time() - meta.get("ts", 0) < 90 * 86400:
            return json.loads(data_p.read_text(encoding="utf-8")), meta
    errors = []
    with K._client(120) as c:
        for ds in SNCF_DATASETS:
            url = SNCF_EXPORT.format(ds=ds)
            try:
                txt, full, _st = K._get(c, url, {"delimiter": ";"}, expect="text")
            except K.ConnectorError as e:
                errors.append(f"{ds} : {e.message}")
                continue
            reader = csv.DictReader(io.StringIO(txt.lstrip("﻿")), delimiter=";")
            cols = reader.fieldnames or []
            pts = []
            for r in reader:
                ll = _latlon_from_row(r)
                if ll and _is_passenger(r):
                    nom = next((v for k, v in r.items() if _norm(k) in ("nom", "libelle", "nom_gare", "alias_libelle_noncontraint", "libellecourt")), "")
                    pts.append({"nom": nom, "lat": round(ll[0], 6), "lon": round(ll[1], 6)})
            if pts:
                meta = {"dataset": ds, "url": full, "colonnes": cols[:30], "gares": len(pts), "ts": time.time(),
                        "telecharge_le": date.today().isoformat()}
                data_p.write_text(json.dumps(pts), encoding="utf-8")
                meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                return pts, meta
            errors.append(f"{ds} : aucune gare avec coordonnées (colonnes : {', '.join(cols[:12])})")
    raise K.ConnectorError("gares SNCF indisponibles — " + " ; ".join(errors))


def nearest(points: list[dict], lat: float, lon: float) -> tuple[dict | None, float | None]:
    best, bd = None, None
    for p in points:
        if abs(p["lat"] - lat) > 0.6 or abs(p["lon"] - lon) > 0.9:  # pré-filtre rapide (~60 km)
            continue
        d = distance_km(lat, lon, p["lat"], p["lon"])
        if bd is None or d < bd:
            best, bd = p, d
    return best, bd


# -------------------------------------------------------- transports (OSM)
OVERPASS_QUERY = """[out:json][timeout:90];
(
  node["highway"="bus_stop"]({s},{w},{n},{e});
  node["public_transport"="platform"]({s},{w},{n},{e});
  node["railway"~"^(station|halt|tram_stop)$"]({s},{w},{n},{e});
  node["station"~"^(subway|light_rail)$"]({s},{w},{n},{e});
);
out;"""


def fetch_stops(bbox: tuple[float, float, float, float]) -> list[dict]:
    """Arrêts de transport collectif dans une emprise (sud, ouest, nord, est)."""
    s, w, n, e = bbox
    q = OVERPASS_QUERY.format(s=s, w=w, n=n, e=e)
    r, essais = None, []
    with K._client(120) as c:
        for url in [OVERPASS_URL, *OVERPASS_SECOURS]:
            try:
                r = c.post(url, data={"data": q})
            except K.httpx.HTTPError as ex:
                essais.append(f"{url.split('/')[2]} injoignable ({type(ex).__name__})")
                r = None
                continue
            if r.status_code in (429, 502, 503, 504):  # surcharge : serveur suivant
                essais.append(f"{url.split('/')[2]} HTTP {r.status_code}")
                continue
            break
    if r is None or r.status_code in (429, 502, 503, 504):
        raise K.ConnectorError("Overpass : serveurs surchargés (" + " ; ".join(essais) + "), réessayer plus tard",
                               OVERPASS_URL, r.status_code if r is not None else None)
    if r.status_code >= 400:
        raise K.ConnectorError(f"Overpass : HTTP {r.status_code}", OVERPASS_URL, r.status_code, r.text[:300])
    try:
        els = r.json().get("elements", [])
    except ValueError:
        raise K.ConnectorError("Overpass : réponse non JSON", OVERPASS_URL, r.status_code, r.text[:300])
    out, seen = [], set()
    for el in els:
        if "lat" not in el:
            continue
        t = el.get("tags", {})
        lourd = (t.get("railway") in ("station", "halt") or t.get("station") in ("subway", "light_rail")
                 or t.get("railway") == "tram_stop")
        key = (round(el["lat"], 4), round(el["lon"], 4))  # dédoublonnage arrêt / quai superposés
        if key in seen:
            continue
        seen.add(key)
        out.append({"lat": el["lat"], "lon": el["lon"], "lourd": lourd, "train": t.get("railway") in ("station", "halt")
                    and t.get("station") not in ("subway", "light_rail"), "nom": t.get("name", "")})
    return out


def desserte(stops: list[dict], lat: float, lon: float) -> dict:
    """Classe la desserte : forte si un mode lourd (métro, tram, train) est à moins de 600 m ou si au moins
    8 arrêts sont à moins de 400 m ; moyenne si au moins 2 arrêts à moins de 400 m ; faible sinon."""
    n400 = sum(1 for s in stops if distance_km(lat, lon, s["lat"], s["lon"]) <= 0.4)
    lourd = [distance_km(lat, lon, s["lat"], s["lon"]) for s in stops if s["lourd"]]
    d_lourd = min(lourd) if lourd else None
    if (d_lourd is not None and d_lourd <= 0.6) or n400 >= 8:
        cl = "forte"
    elif n400 >= 2:
        cl = "moyenne"
    else:
        cl = "faible"
    return {"classe": cl, "arrets_400m": n400, "mode_lourd_km": round(d_lourd, 2) if d_lourd is not None else None}


# ------------------------------------------------------------------- PVGIS
def _find_key(o, pred):
    if isinstance(o, dict):
        for k, v in o.items():
            if pred(k) and isinstance(v, (int, float)):
                return v
            r = _find_key(v, pred)
            if r is not None:
                return r
    elif isinstance(o, list):
        for x in o:
            r = _find_key(x, pred)
            if r is not None:
                return r
    return None


def fetch_pvgis(lat: float, lon: float) -> tuple[float, dict]:
    """Irradiation annuelle sur plan incliné optimal (kWh/m²/an) et productible (kWh/kWc/an)."""
    params = {"lat": round(lat, 3), "lon": round(lon, 3), "peakpower": 1, "loss": 14, "optimalangles": 1,
              "outputformat": "json"}
    with K._client(60) as c:
        data, full, st = K._get(c, PVGIS_URL, params)
    h = _find_key(data, lambda k: k.replace(" ", "") in ("H(i)_y", "H(i_opt)_y"))
    e = _find_key(data, lambda k: k == "E_y")
    diag = {"url": full, "statut": st, "H(i)_y": h, "E_y": e}
    if h is None:
        raise K.ConnectorError("PVGIS : champ « H(i)_y » absent de la réponse", full, st, json.dumps(data)[:300])
    return float(h), diag


# ---------------------------------------------------------------- risques
FORT = re.compile(r"fort|elev|important", re.I)
MOYEN = re.compile(r"moyen|modere", re.I)


def fetch_risques_adresse(lat: float, lon: float) -> tuple[dict, dict]:
    """Rapport de risques Géorisques au point (inondation, retrait-gonflement des argiles)."""
    with K._client(45) as c:
        data, full, st = K._get(c, f"{GEORISQUES_URL.rstrip('/')}/resultats_rapport_risque", {"latlon": f"{lon},{lat}"})
    found = {}

    def walk(o, path=""):
        if isinstance(o, dict):
            low = {k.lower(): v for k, v in o.items()}
            p = _norm(path)
            if "present" in low or "libellestatutadresse" in low or "libelle" in low:
                found[p] = {k: v for k, v in o.items() if not isinstance(v, (dict, list))}
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, f"{path}[{i}]")
    walk(data)
    out = {}
    inond = {k: v for k, v in found.items() if "inond" in k or "remonteenappe" in k.replace("_", "")}
    if inond:
        out["risque_inondation"] = any(bool(v.get("present")) for k, v in inond.items() if "inond" in k)
    arg = next((v for k, v in found.items() if "argile" in k), None)
    if arg:
        txt = " ".join(str(x) for x in arg.values())
        out["alea_argiles"] = "fort" if FORT.search(_norm(txt)) else "moyen" if MOYEN.search(_norm(txt)) else "faible"
    diag = {"url": full, "statut": st, "rubriques_detectees": sorted(found)[:30]}
    if not out:
        raise K.ConnectorError("rapport de risques : aucune rubrique inondation / argiles reconnue", full, st, json.dumps(data)[:400])
    return out, diag


def fetch_risques_commune(code_insee: str) -> tuple[dict, dict]:
    """Repli : risques recensés sur la commune (base GASPAR)."""
    with K._client(45) as c:
        data, full, st = K._get(c, f"{GEORISQUES_URL.rstrip('/')}/gaspar/risques", {"code_insee": code_insee})
    libs = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if "libelle" in k.lower() and isinstance(v, str):
                    libs.append(v)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(data)
    txt = _norm(" ".join(libs))
    out = {"risque_inondation": "inondation" in txt}
    return out, {"url": full, "statut": st, "risques_commune": sorted(set(libs))[:20]}


# ------------------------------------------- urbanisme (Géoportail de l'urbanisme)
# Code CNIG « destdomi » (vocation dominante) -> vocation FricheScore. Liste à confirmer sur les données réelles :
# en l'absence de code exploitable, la vocation est déduite du libellé long de la zone.
DESTDOMI = {"01": "habitat", "02": "activite", "03": "equipement", "04": "naturelle", "05": "naturelle", "06": "naturelle"}
VOC_MOTS = [("activit", "activite"), ("economi", "activite"), ("industri", "activite"), ("artisan", "activite"),
            ("equipement", "equipement"), ("service public", "equipement"), ("habitat", "habitat"), ("residenti", "habitat"),
            ("logement", "habitat"), ("mixte", "mixte"), ("centre", "mixte"), ("naturel", "naturelle"), ("agricol", "naturelle")]


def _geom(lat: float, lon: float) -> str:
    return json.dumps({"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]})


def _features(data) -> list[dict]:
    if isinstance(data, dict):
        return [f.get("properties") or {} for f in data.get("features") or []]
    return []


def plu_from_props(props: dict) -> dict:
    """Zone, vocation et libellés à partir des propriétés d'une zone du GPU."""
    tz = str(props.get("typezone") or "").strip().upper()
    zone = "AU" if tz.startswith("AU") else (tz[:1] if tz[:1] in ("U", "A", "N") else None)
    code = str(props.get("destdomi") or "").strip()[:2]
    voc = DESTDOMI.get(code)
    if voc is None:
        txt = _norm(f"{props.get('libelong') or ''} {props.get('libelle') or ''}")
        voc = next((v for mot, v in VOC_MOTS if mot in txt), None)
    if voc is None and zone == "U":
        voc = "mixte"  # zone urbaine sans vocation spécialisée identifiée
    if zone in ("A", "N") and voc is None:
        voc = "naturelle"
    return {"plu_zone": zone, "plu_vocation": voc, "libelle": props.get("libelle"), "libelong": props.get("libelong"),
            "typezone": props.get("typezone"), "destdomi": props.get("destdomi")}


def fetch_gpu(lat: float, lon: float) -> tuple[dict, dict]:
    with K._client(45) as c:
        z, full, st = K._get(c, f"{APICARTO_URL.rstrip('/')}/gpu/zone-urba", {"geom": _geom(lat, lon)})
        feats = _features(z)
        out, diag = {}, {"url": full, "statut": st, "zones": len(feats)}
        if feats:
            p = plu_from_props(feats[0])
            diag.update({k: p[k] for k in ("libelle", "libelong", "typezone", "destdomi")})
            if p["plu_zone"]:
                out["plu_zone"] = p["plu_zone"]
            if p["plu_vocation"]:
                out["plu_vocation"] = p["plu_vocation"]
        try:
            sup, _f, _s = K._get(c, f"{APICARTO_URL.rstrip('/')}/gpu/assiette-sup-s", {"geom": _geom(lat, lon)})
            out["servitudes_count"] = len(_features(sup))
            diag["servitudes"] = sorted({str(f.get("suptype") or f.get("typeass") or "") for f in _features(sup)} - {""})[:10]
        except K.ConnectorError as e:
            diag["servitudes_erreur"] = e.message
    if not feats:
        raise K.ConnectorError("aucun document d'urbanisme publié sur le Géoportail de l'urbanisme à cet endroit "
                               "(commune au RNU ou PLU non numérisé)", full, st)
    return out, diag


def fetch_nature(lat: float, lon: float) -> tuple[dict, dict]:
    """Natura 2000 (habitats et oiseaux) et ZNIEFF (types 1 et 2) au point, via l'API Carto module Nature."""
    res, diag = {}, {}
    with K._client(45) as c:
        for key, paths in (("natura2000", ("nature/natura-habitat", "nature/natura-oiseaux")),
                           ("znieff", ("nature/znieff1", "nature/znieff2"))):
            n, ok = 0, False
            for path in paths:
                try:
                    data, _f, _s = K._get(c, f"{APICARTO_URL.rstrip('/')}/{path}", {"geom": _geom(lat, lon)})
                    n += len(_features(data))
                    ok = True
                except K.ConnectorError as e:
                    diag[path] = e.message
            if ok:
                res[key] = n > 0
                diag[key] = n
    if not res:
        raise K.ConnectorError("API Carto (module Nature) injoignable : " + " ; ".join(f"{k} : {v}" for k, v in diag.items()))
    return res, diag


# ------------------------------------------- milieux humides (Géoplateforme, WFS)
def humid_layer(refresh: bool = False) -> str:
    """Nom de la couche WFS des milieux humides, trouvé dans le catalogue de la Géoplateforme (mis en cache)."""
    forced = os.environ.get("ZONES_HUMIDES_COUCHE")
    if forced:
        return forced
    p = cache_dir() / "wfs_milieux_humides.json"
    if p.exists() and not refresh:
        d = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - d.get("ts", 0) < 90 * 86400 and d.get("couche"):
            return d["couche"]
    with K._client(120) as c:
        txt, full, _st = K._get(c, GEOPF_WFS_URL, {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetCapabilities"}, expect="text")
    names = re.findall(r"<(?:wfs:)?Name>([^<]+)</(?:wfs:)?Name>", txt)
    cands = [n for n in names if "humid" in n.lower()]
    if not cands:
        raise K.ConnectorError("aucune couche « milieux humides » dans le catalogue WFS de la Géoplateforme", full)

    def score(n):
        x = n.lower()
        return (("milieu" in x) * 3 + ("seuil" in x or "potenti" in x or "pot" in x) * 2 + ("proba" in x) * 1
                - ("drom" in x or "outre" in x) * 5, -len(x))
    best = max(cands, key=score)
    p.write_text(json.dumps({"couche": best, "candidates": cands[:30], "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
    return best


def fetch_zones_humides(lat: float, lon: float, marge_m: float = 30) -> tuple[dict, dict]:
    """Présence d'un milieu humide prélocalisé sur le site (marge de 30 m) : « probable » si présent, « non » sinon."""
    layer = humid_layer()
    dlat = marge_m / 111_000
    dlon = marge_m / (111_000 * max(0.2, math.cos(math.radians(lat))))
    params = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": layer, "COUNT": 5,
              "OUTPUTFORMAT": "application/json", "SRSNAME": "CRS:84",
              "BBOX": f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat},CRS:84"}
    with K._client(60) as c:
        data, full, st = K._get(c, GEOPF_WFS_URL, params)
    n = len(data.get("features") or []) if isinstance(data, dict) else 0
    return {"zone_humide": "probable" if n else "non"}, {"url": full, "statut": st, "couche": layer, "objets": n}


# ---------------------------------------------------------- orchestration
def enrich_sites(recs: list, adresse: bool = True, progress=None) -> tuple[dict[str, dict], dict]:
    """Calcule les indicateurs de site. `recs` : Records avec id, lat, lon, code_insee.
    Retourne ({id: {clé: (valeur, Provenance)}}, rapport)."""
    today = date.today().isoformat()
    sites = [r for r in recs if r.has("lat") and r.has("lon")]
    out: dict[str, dict] = {r.get("id"): {} for r in sites}
    rep = {"sites": len(sites), "indicateurs": {}, "erreurs": [], "sources": {}}

    def tick(e, f=0, t=0):
        if progress:
            progress(e, f, t)

    def put(sid, k, v, p):
        out[sid][k] = (v, p)
        rep["indicateurs"][k] = rep["indicateurs"].get(k, 0) + 1

    # Gares SNCF
    tick("Gares SNCF", 0, 1)
    stations = []
    try:
        stations, meta = load_stations()
        rep["sources"]["gares"] = {k: meta.get(k) for k in ("dataset", "gares", "telecharge_le")}
    except K.ConnectorError as e:
        rep["erreurs"].append(f"Gares SNCF : {e.message} — repli sur les gares OpenStreetMap")
    # Arrêts OSM, une requête par commune
    by_com: dict[str, list] = {}
    for r in sites:
        by_com.setdefault(r.get("code_insee") or "?", []).append(r)
    for i, (code, rs) in enumerate(by_com.items()):
        tick(f"Transports collectifs (OpenStreetMap) : {code}", i, len(by_com))
        lats, lons = [r.get("lat") for r in rs], [r.get("lon") for r in rs]
        bbox = (min(lats) - 0.012, min(lons) - 0.018, max(lats) + 0.012, max(lons) + 0.018)
        try:
            stops = fetch_stops(bbox)
        except K.ConnectorError as e:
            rep["erreurs"].append(f"{code} : transports collectifs — {e.message}")
            stops = None
        for r in rs:
            sid, la, lo = r.get("id"), r.get("lat"), r.get("lon")
            if stops is not None:
                dd = desserte(stops, la, lo)
                p = Provenance(OSM, today, "moyen", f"{dd['arrets_400m']} arrêt(s) à moins de 400 m ; mode lourd à "
                               f"{dd['mode_lourd_km'] if dd['mode_lourd_km'] is not None else '> 1,5'} km")
                put(sid, "desserte_tc", dd["classe"], p)
                put(sid, "nb_arrets_tc_400m", dd["arrets_400m"], p)
            g, d = nearest(stations, la, lo) if stations else (None, None)
            if g is not None:
                put(sid, "distance_gare_km", round(d, 2), Provenance("Gares de voyageurs — SNCF open data", today, "eleve",
                                                                     f"gare la plus proche : {g['nom']} (à vol d'oiseau)"))
            elif stops:
                trains = [s for s in stops if s["train"]]
                g2, d2 = nearest(trains, la, lo)
                if g2 is not None:
                    put(sid, "distance_gare_km", round(d2, 2), Provenance(OSM, today, "moyen", f"gare la plus proche : {g2['nom']}"))
        time.sleep(1.0)  # politesse envers le serveur public Overpass
    # PVGIS par carreau de 0,1°
    cells: dict[tuple, list] = {}
    for r in sites:
        cells.setdefault((round(r.get("lat"), 1), round(r.get("lon"), 1)), []).append(r)
    for i, (cell, rs) in enumerate(cells.items()):
        tick("Ensoleillement (PVGIS)", i, len(cells))
        try:
            h, _d = fetch_pvgis(cell[0], cell[1])
            for r in rs:
                put(r.get("id"), "irradiation_kwh_m2", round(h), Provenance("PVGIS 5.3 — Commission européenne (JRC)", today,
                                                                            "eleve", "plan incliné optimal, carreau de 0,1°"))
        except K.ConnectorError as e:
            rep["erreurs"].append(f"PVGIS {cell} : {e.message}")
    # Urbanisme, espaces naturels protégés, milieux humides : 3 sources par site, 4 sites en parallèle
    today_p = {
        "gpu": lambda: Provenance("Géoportail de l'urbanisme — API Carto IGN", today, "eleve", "zone du document d'urbanisme au point"),
        "gpu_voc": lambda: Provenance("Géoportail de l'urbanisme — API Carto IGN", today, "moyen",
                                      "vocation déduite du code CNIG ou du libellé de la zone"),
        "nature": lambda: Provenance("INPN — API Carto IGN (module Nature)", today, "eleve", "intersection au point"),
        "zh": lambda: Provenance("Cartographie nationale des milieux humides (OFB/PatriNat) — Géoplateforme", today, "moyen",
                                 "prélocalisation : présence probable, à confirmer sur le terrain"),
    }

    def per_site(r):
        res, errs = {}, []
        la, lo = r.get("lat"), r.get("lon")
        for name, fn in (("urbanisme", fetch_gpu), ("nature", fetch_nature), ("milieux humides", fetch_zones_humides)):
            try:
                res[name] = fn(la, lo)[0]
            except K.ConnectorError as e:
                errs.append(f"{r.get('id')} : {name} — {e.message}")
        return r, res, errs
    done = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for r, res, errs in ex.map(per_site, sites):
            done += 1
            if done % 5 == 0 or done == len(sites):
                tick("Urbanisme, Natura 2000 / ZNIEFF, milieux humides", done, len(sites))
            sid = r.get("id")
            if len(rep["erreurs"]) < 300:
                rep["erreurs"] += errs
            u = res.get("urbanisme") or {}
            if "plu_zone" in u:
                put(sid, "plu_zone", u["plu_zone"], today_p["gpu"]())
            if "plu_vocation" in u:
                put(sid, "plu_vocation", u["plu_vocation"], today_p["gpu_voc"]())
            if "servitudes_count" in u:
                put(sid, "servitudes_count", u["servitudes_count"], today_p["gpu"]())
            for k, v in (res.get("nature") or {}).items():
                put(sid, k, v, today_p["nature"]())
            if "zone_humide" in (res.get("milieux humides") or {}):
                put(sid, "zone_humide", res["milieux humides"]["zone_humide"], today_p["zh"]())
    # Risques
    gaspar_cache: dict[str, dict | None] = {}
    for i, r in enumerate(sites):
        tick("Risques naturels (Géorisques)", i, len(sites))
        sid = r.get("id")
        vals, src, conf = None, None, None
        if adresse:
            try:
                vals, _ = fetch_risques_adresse(r.get("lat"), r.get("lon"))
                src, conf = "Rapport de risques à l'adresse — Géorisques", "eleve"
            except K.ConnectorError as e:
                if len(rep["erreurs"]) < 200:
                    rep["erreurs"].append(f"{sid} : rapport de risques — {e.message} (repli sur la commune)")
            time.sleep(1.05)  # limite annoncée : 1 appel par seconde
        if vals is None and r.get("code_insee"):
            code = r.get("code_insee")
            if code not in gaspar_cache:
                try:
                    gaspar_cache[code] = fetch_risques_commune(code)[0]
                except K.ConnectorError as e:
                    gaspar_cache[code] = None
                    rep["erreurs"].append(f"{code} : risques de la commune (GASPAR) — {e.message}")
            vals = gaspar_cache[code]
            src, conf = "Risques recensés sur la commune — Géorisques (GASPAR)", "moyen"
        for k, v in (vals or {}).items():
            put(sid, k, v, Provenance(src, today, conf))
    return out, rep


def test_site(lat: float, lon: float, code_insee: str) -> dict:
    """Diagnostic de chaque source pour un point."""
    det = {}
    t0 = time.monotonic()
    try:
        st, meta = load_stations()
        g, d = nearest(st, lat, lon)
        det["gares_sncf"] = {"ok": g is not None, "valeur": f"{g['nom']} à {d:.2f} km" if g else None,
                             "detection": {k: meta.get(k) for k in ("dataset", "url", "gares", "colonnes")}}
    except K.ConnectorError as e:
        det["gares_sncf"] = {"ok": False, "erreur": e.message}
    try:
        stops = fetch_stops((lat - 0.012, lon - 0.018, lat + 0.012, lon + 0.018))
        dd = desserte(stops, lat, lon)
        det["transports_osm"] = {"ok": True, "valeur": dd["classe"], "detection": {**dd, "arrets_dans_l_emprise": len(stops)}}
    except K.ConnectorError as e:
        det["transports_osm"] = {"ok": False, "erreur": e.message}
    try:
        h, d = fetch_pvgis(lat, lon)
        det["pvgis"] = {"ok": True, "valeur": f"{h:.0f} kWh/m²/an", "detection": d}
    except K.ConnectorError as e:
        det["pvgis"] = {"ok": False, "erreur": e.message, "detection": {"apercu": e.body}}
    try:
        v, d = fetch_risques_adresse(lat, lon)
        det["risques_adresse"] = {"ok": True, "valeur": v, "detection": d}
    except K.ConnectorError as e:
        det["risques_adresse"] = {"ok": False, "erreur": e.message, "detection": {"apercu": e.body}}
    try:
        v, d = fetch_risques_commune(code_insee)
        det["risques_commune"] = {"ok": True, "valeur": v, "detection": d}
    except K.ConnectorError as e:
        det["risques_commune"] = {"ok": False, "erreur": e.message}
    for name, fn in (("urbanisme_gpu", fetch_gpu), ("natura2000_znieff", fetch_nature), ("milieux_humides", fetch_zones_humides)):
        try:
            v, d = fn(lat, lon)
            det[name] = {"ok": True, "valeur": v, "detection": d}
        except K.ConnectorError as e:
            det[name] = {"ok": False, "erreur": e.message, "url": e.url, "detection": {"apercu": e.body}}
    ok = [k for k, v in det.items() if v.get("ok")]
    return {"source": "sites", "ok": bool(ok), "duree_ms": round((time.monotonic() - t0) * 1000),
            "resultat": {"sources_ok": ok, "sources_en_echec": [k for k in det if k not in ok]}, "detail": det,
            "erreur": None if ok else "aucune source d'indicateurs de site n'a répondu"}
