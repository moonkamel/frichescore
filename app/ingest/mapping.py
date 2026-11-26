"""Correspondance entre champs sources (Cartofriches, exports CSV) et indicateurs FricheScore.

Les noms de champs Cartofriches ci-dessous suivent la structure publiée par le
Cerema (API Données foncières / export open data). Ils sont lus de manière
tolérante : un champ absent est simplement ignoré et signalé dans le rapport
d'import. À valider sur un export réel avant un usage en production.
"""
from __future__ import annotations

from datetime import date

from ..scoring.indicators import CATALOG, coerce, default_confidence
from ..scoring.record import Provenance, Record

# alias de colonne -> clé FricheScore
ALIASES = {
    "site_id": "id", "identifiant": "id", "id_site": "id",
    "site_nom": "nom", "nom_site": "nom",
    "comm_insee": "code_insee", "insee": "code_insee", "code_commune": "code_insee",
    "comm_nom": "commune", "nom_commune": "commune",
    "dep": "departement", "code_departement": "departement", "dep_code": "departement",
    "epci_nom": "epci", "nom_epci": "epci",
    "latitude": "lat", "longitude": "lon", "y": "lat", "x": "lon",
    "site_surface": "surface_m2", "unite_fonciere_surface": "surface_m2", "surface": "surface_m2",
    "site_type": "type_friche", "type": "type_friche",
    "site_statut": "statut_cartofriches",
    "bati_surface": "bati_emprise_m2",
    "bati_etat": "bati_etat",
    "activite_libelle": "activite_passee",
    "urba_zone_type": "plu_zone",
    "proprio_nombre": "nb_proprietaires",
}

TYPE_MAP = {
    "friche industrielle": "industrielle", "friche d'activité": "industrielle", "friche commerciale": "commerciale",
    "friche ferroviaire": "ferroviaire", "friche portuaire": "portuaire", "friche militaire": "militaire",
    "friche hospitalière": "hospitaliere", "friche d'habitat": "habitat", "friche tertiaire": "tertiaire",
    "friche carrière ou mine": "carriere", "friche agro-industrielle": "industrielle", "friche logistique": "logistique",
    "friche cultuelle": "equipement", "friche enseignement": "equipement", "friche loisir tourisme hôtellerie": "commerciale",
    "mixte": "inconnu",
}

ETAT_MAP = {"bon": "bon", "bon état": "bon", "moyen": "moyen", "dégradé": "degrade", "degrade": "degrade",
            "très dégradé": "ruine", "ruine": "ruine", "en ruine": "ruine"}


def _norm_type(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in CATALOG["type_friche"][4]:
        return s
    return TYPE_MAP.get(s, "inconnu")


def _norm_zone(v):
    if not v:
        return None
    s = str(v).strip().upper()
    for z in ("AU", "U", "A", "N"):
        if s.startswith(z):
            return z
    return "inconnu"


def _norm_statut(v):
    if not v:
        return None
    s = str(v).lower()
    return "verifiee" if ("vérifi" in s or "verifi" in s or "avec projet" in s or "sans projet" in s) and "non" not in s else "non_verifiee"


def row_to_record(row: dict, origine_label: str, default_date: str | None = None,
                  default_conf: str | None = None) -> tuple[Record, list[str]]:
    """Convertit une ligne (CSV ou JSON d'API) en Record tracé.
    Retourne aussi la liste des colonnes ignorées."""
    rec = Record()
    ignored = []
    today = default_date or date.today().isoformat()
    lower = {str(k).strip(): v for k, v in row.items()}
    for col, raw in lower.items():
        if col.endswith(("__source", "__date", "__confiance")):
            continue
        key = col if col in CATALOG or col == "id" else ALIASES.get(col.lower())
        if key is None:
            ignored.append(col)
            continue
        if key == "type_friche":
            val = _norm_type(raw)
        elif key == "plu_zone":
            val = _norm_zone(raw)
        elif key == "statut_cartofriches":
            val = _norm_statut(raw)
        elif key == "bati_etat":
            val = ETAT_MAP.get(str(raw).strip().lower(), "inconnu") if raw not in (None, "") else None
        elif key == "id":
            val = str(raw).strip() if raw not in (None, "") else None
        else:
            val = coerce(key, raw)
        if val is None:
            continue
        src = lower.get(f"{col}__source") or (f"{CATALOG[key][2]} via {origine_label}" if key in CATALOG else origine_label)
        conf = lower.get(f"{col}__confiance") or default_conf or default_confidence(key)
        if key == "basias_count":
            conf = lower.get(f"{col}__confiance") or "faible"  # BASIAS : inventaire historique non vérifié
        rec.set(key, val, Provenance(src, lower.get(f"{col}__date") or today, conf))
    if rec.has("code_insee") and not rec.has("departement"):
        ci = str(rec.get("code_insee"))
        rec.values["departement"] = ci[:3] if ci.startswith("97") else ci[:2]
    return rec, ignored
