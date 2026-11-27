"""Calendrier des financements et des échéances ZAN : lecture, validation, écriture.

Le fichier livré (`app/data/funding_windows.json`) sert de modèle. Au premier
usage, il est copié à côté de la base de données (`FRICHESCORE_CALENDAR`, par
défaut `<dossier de la base>/funding_windows.json`) : c'est cette copie, placée
dans le volume Docker, que l'utilisateur modifie depuis l'interface.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path

from .config import USAGES

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "data" / "funding_windows.json"
DISPOSITIFS = {"fonds_vert_recyclage", "fonds_vert_renaturation", "ademe_friches_polluees", "sites_cles_en_main",
               "cre_terrains_degrades", "fonds_regional", "autre"}
CONDITIONS = {"bilan_deficitaire", "pollue", "surface_min_ha"}
STATUTS = {"verifie", "a_verifier"}
DEFAULT_TYPES = {"subvention_etat": "Subvention de l'État", "appel_a_projets": "Appel à projets national",
                 "appel_offres_cre": "Appel d'offres CRE", "regional": "Dispositif régional"}


def path() -> Path:
    env = os.environ.get("FRICHESCORE_CALENDAR")
    if env:
        return Path(env)
    from ..db import DB_PATH  # import local : évite une dépendance circulaire
    from .. import tenant
    o = tenant.org()
    if o in (None, tenant.LOCAL):
        return DB_PATH.parent / "funding_windows.json"
    return DB_PATH.parent / "calendriers" / f"{o}.json"  # un calendrier par organisme


def load() -> dict:
    p = path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        if DEFAULT_FILE.exists():
            shutil.copyfile(DEFAULT_FILE, p)
        else:  # modèle absent de l'installation : calendrier vide plutôt qu'une erreur
            return {"fenetres": [], "echeances_reglementaires": [], "types": dict(DEFAULT_TYPES)}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("types", dict(DEFAULT_TYPES))
    for w in data.get("fenetres", []):
        w.setdefault("type", "subvention_etat")
        w.setdefault("statut_verification", "a_verifier")
        w.setdefault("conditions", {})
    return data


def _date(v, label, errs):
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        errs.append(f"{label} : date invalide « {v} » (format attendu AAAA-MM-JJ)")
        return None


def validate(data: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["le calendrier doit être un objet JSON"]
    wins = data.get("fenetres")
    if not isinstance(wins, list):
        errs.append("« fenetres » doit être une liste")
        wins = []
    types = data.get("types") or DEFAULT_TYPES
    seen = set()
    for i, w in enumerate(wins, 1):
        lab = f"Fenêtre {i} ({w.get('nom') or w.get('id') or 'sans nom'})"
        if not isinstance(w, dict):
            errs.append(f"Fenêtre {i} : objet attendu")
            continue
        wid = str(w.get("id") or "").strip()
        if not wid:
            errs.append(f"{lab} : identifiant manquant")
        elif wid in seen:
            errs.append(f"{lab} : identifiant « {wid} » en double")
        seen.add(wid)
        if not str(w.get("nom") or "").strip():
            errs.append(f"{lab} : nom manquant")
        o = _date(w.get("ouverture"), f"{lab} — ouverture", errs)
        c = _date(w.get("cloture"), f"{lab} — clôture", errs)
        if o and c and o > c:
            errs.append(f"{lab} : la date d'ouverture est postérieure à la clôture")
        us = w.get("usages")
        if not isinstance(us, list) or not us:
            errs.append(f"{lab} : au moins un usage éligible est requis")
        else:
            bad = [u for u in us if u not in USAGES]
            if bad:
                errs.append(f"{lab} : usage(s) inconnu(s) {', '.join(map(str, bad))} (attendus : {', '.join(USAGES)})")
        if w.get("dispositif") not in DISPOSITIFS:
            errs.append(f"{lab} : dispositif « {w.get('dispositif')} » inconnu (attendus : {', '.join(sorted(DISPOSITIFS))})")
        if w.get("type") not in types:
            errs.append(f"{lab} : type « {w.get('type')} » inconnu (attendus : {', '.join(types)})")
        if w.get("statut_verification") not in STATUTS:
            errs.append(f"{lab} : statut de vérification « {w.get('statut_verification')} » invalide (verifie / a_verifier)")
        cond = w.get("conditions") or {}
        if not isinstance(cond, dict):
            errs.append(f"{lab} : « conditions » doit être un objet")
        else:
            for k, v in cond.items():
                if k not in CONDITIONS:
                    errs.append(f"{lab} : condition « {k} » inconnue (attendues : {', '.join(sorted(CONDITIONS))})")
                elif k == "surface_min_ha" and not isinstance(v, (int, float)):
                    errs.append(f"{lab} : surface_min_ha doit être un nombre")
        if w.get("date_verification"):
            _date(w["date_verification"], f"{lab} — date de vérification", errs)
    for i, e in enumerate(data.get("echeances_reglementaires") or [], 1):
        lab = f"Échéance {i} ({e.get('nom', '')})"
        if not e.get("id") or not e.get("nom"):
            errs.append(f"{lab} : identifiant et nom requis")
        _date(e.get("date"), f"{lab} — date", errs)
    return errs


def save(data: dict) -> list[str]:
    errs = validate(data)
    if errs:
        return errs
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return []


def reset() -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_FILE, p)
