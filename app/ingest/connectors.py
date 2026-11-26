"""Connecteurs vers les sources publiques ouvertes.

Chaque connecteur renvoie des Records tracés (source + date + confiance).
Ils nécessitent un accès réseau ; en cas d'échec, l'erreur est remontée
telle quelle à l'utilisateur, sans inventer de donnée.

Points d'accès utilisés (à vérifier si les API évoluent) :
  * Cartofriches — API Données foncières du Cerema :
      https://apidf.cerema.fr/cartofriches/friches/?code_insee=<INSEE>
  * Géorisques API v1 (ex-BASIAS = CASIAS, ex-BASOL = instructions SSP, ICPE) :
      https://georisques.gouv.fr/api/v1/ssp/casias?latlon=<lon>,<lat>&rayon=<m>
      https://georisques.gouv.fr/api/v1/ssp/instructions?latlon=...
      https://georisques.gouv.fr/api/v1/ssp/conclusions_sis?latlon=...
      https://georisques.gouv.fr/api/v1/installations_classees?latlon=...
  * DVF géolocalisé (Etalab) :
      https://files.data.gouv.fr/geo-dvf/latest/csv/<annee>/communes/<dep>/<insee>.csv
"""
from __future__ import annotations

import csv
import io
import os
import statistics
from datetime import date

import httpx

from ..scoring.record import Provenance, Record
from .mapping import row_to_record

CARTOFRICHES_URL = os.environ.get("CARTOFRICHES_URL", "https://apidf.cerema.fr/cartofriches/friches/")
GEORISQUES_URL = os.environ.get("GEORISQUES_URL", "https://georisques.gouv.fr/api/v1")
GEODVF_URL = os.environ.get("GEODVF_URL", "https://files.data.gouv.fr/geo-dvf/latest/csv")
TIMEOUT = float(os.environ.get("FRICHESCORE_HTTP_TIMEOUT", "20"))


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "FricheScore/0.1 (outil local)"})


# ------------------------------------------------------------- Cartofriches
def parse_cartofriches(payload: dict | list) -> list[dict]:
    """Accepte une page JSON paginée ({results: [...]}) ou GeoJSON ({features: [...]})."""
    if isinstance(payload, list):
        return payload
    if "features" in payload:
        out = []
        for f in payload["features"]:
            props = dict(f.get("properties") or {})
            geom = f.get("geometry") or {}
            if geom.get("type") == "Point":
                props.setdefault("longitude", geom["coordinates"][0])
                props.setdefault("latitude", geom["coordinates"][1])
            out.append(props)
        return out
    return list(payload.get("results") or [])


def cartofriches_records(rows: list[dict]) -> list[Record]:
    out = []
    today = date.today().isoformat()
    for row in rows:
        rec, _ = row_to_record(row, "API Cartofriches (Cerema)", today)
        if not rec.has("id"):
            continue
        rec.values["id"] = f"CF-{rec.get('id')}"
        # Champs pollution éventuellement présents sous forme de listes d'identifiants
        for src_key, key in (("l_basol", "basol"), ("l_basias", "basias_count"), ("l_icpe", "icpe_count")):
            if src_key in row:
                v = row.get(src_key)
                items = [x for x in (v if isinstance(v, list) else str(v or "").replace(";", "|").split("|")) if str(x).strip()]
                val = bool(items) if key == "basol" else len(items)
                rec.set(key, val, Provenance(f"{key.split('_')[0].upper()} via Cartofriches", today,
                                             "faible" if key == "basias_count" else "eleve"))
        out.append(rec)
    return out


def fetch_cartofriches(code_insee: str) -> list[Record]:
    rows: list[dict] = []
    url = CARTOFRICHES_URL
    params = {"code_insee": code_insee}
    with _client() as c:
        while url:
            r = c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            rows += parse_cartofriches(data)
            url = data.get("next") if isinstance(data, dict) else None
            params = None
    return cartofriches_records(rows)


# --------------------------------------------------------------- Géorisques
def parse_georisques(kind: str, payload: dict) -> int:
    data = payload.get("data")
    if data is None:
        data = payload.get("results", [])
    return len(data) if isinstance(data, list) else int(payload.get("results", 0) or 0)


def enrich_georisques(rec: Record, rayon_m: int = 150) -> Record:
    if not (rec.has("lat") and rec.has("lon")):
        raise ValueError("coordonnées manquantes")
    ll = f"{rec.get('lon')},{rec.get('lat')}"
    today = date.today().isoformat()
    with _client() as c:
        def q(path):
            r = c.get(f"{GEORISQUES_URL}/{path}", params={"latlon": ll, "rayon": rayon_m})
            r.raise_for_status()
            return r.json()
        n = parse_georisques("casias", q("ssp/casias"))
        rec.set("basias_count", n, Provenance("CASIAS (ex-BASIAS) / Géorisques", today, "faible",
                                              f"recherche dans un rayon de {rayon_m} m"))
        n = parse_georisques("instructions", q("ssp/instructions"))
        rec.set("basol", n > 0, Provenance("Instructions SSP (ex-BASOL) / Géorisques", today, "eleve", f"rayon {rayon_m} m"))
        n = parse_georisques("sis", q("ssp/conclusions_sis"))
        rec.set("sis", n > 0, Provenance("SIS / Géorisques", today, "eleve", f"rayon {rayon_m} m"))
        ic = q("installations_classees")
        items = ic.get("data") or []
        rec.set("icpe_count", len(items), Provenance("ICPE / Géorisques", today, "eleve", f"rayon {rayon_m} m"))
        if items:
            it = items[0]
            reg = str(it.get("regime") or it.get("regimeVigueur") or "").lower()
            if reg:
                rec.set("icpe_regime", "autorisation" if "autoris" in reg else "enregistrement" if "enregis" in reg else "declaration",
                        Provenance("ICPE / Géorisques", today, "eleve"))
            sev = str(it.get("statutSeveso") or "").lower()
            if "haut" in sev or "bas" in sev:
                rec.set("seveso", "haut" if "haut" in sev else "bas", Provenance("ICPE / Géorisques", today, "eleve"))
    return rec


# ---------------------------------------------------------------------- DVF
def dvf_indicators(csv_texts: dict[int, str]) -> dict:
    """Calcule les indicateurs de marché à partir des fichiers geo-dvf d'une
    commune ({année: contenu CSV}). Ventes d'appartements et de maisons."""
    by_year: dict[int, list[float]] = {}
    n = 0
    for year, text in csv_texts.items():
        seen = set()
        for row in csv.DictReader(io.StringIO(text)):
            if row.get("nature_mutation") != "Vente" or row.get("type_local") not in ("Appartement", "Maison"):
                continue
            mid = row.get("id_mutation")
            if mid in seen:  # une mutation multi-lots : on ne garde que la 1re ligne
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
                n += 1
    if not by_year:
        return {}
    years = sorted(by_year)
    last = statistics.median(by_year[years[-1]])
    out = {"prix_m2_logement": round(last), "nb_mutations_3ans": sum(len(by_year[y]) for y in years[-3:])}
    if len(years) >= 2:
        first = statistics.median(by_year[years[0]])
        out["evol_prix_3ans_pct"] = round((last / first - 1) * 100, 1)
    out["_annee"] = years[-1]
    return out


def fetch_dvf(code_insee: str, years: list[int]) -> dict:
    dep = code_insee[:3] if code_insee.startswith("97") else code_insee[:2]
    texts = {}
    with _client() as c:
        for y in years:
            r = c.get(f"{GEODVF_URL}/{y}/communes/{dep}/{code_insee}.csv")
            if r.status_code == 200:
                texts[y] = r.text
    return dvf_indicators(texts)
