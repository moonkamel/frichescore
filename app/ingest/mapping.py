"""Correspondance et validation des champs sources (CSV, Cartofriches) vers les
indicateurs FricheScore.

L'import est TOLÉRANT : une cellule invalide est signalée (ligne, colonne,
problème) et ignorée, mais le reste de la ligne est importé. Une ligne n'est
rejetée que si elle ne peut pas être rattachée à un territoire (ni code INSEE,
ni coordonnées).

Les noms de champs Cartofriches suivent la structure publiée par le Cerema
(API Données foncières / export open data), lus de manière tolérante.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from ..scoring.indicators import CATALOG, default_confidence
from ..scoring.record import Provenance, Record

ALIASES = {
    "site_id": "id", "identifiant": "id", "id_site": "id", "ref": "id", "reference": "id",
    "site_nom": "nom", "nom_site": "nom", "nom_friche": "nom",
    "comm_insee": "code_insee", "insee": "code_insee", "code_commune": "code_insee", "codgeo": "code_insee",
    "comm_nom": "commune", "nom_commune": "commune",
    "dep": "departement", "code_departement": "departement", "dep_code": "departement",
    "epci_nom": "epci", "nom_epci": "epci",
    "latitude": "lat", "longitude": "lon", "y": "lat", "x": "lon",
    "site_surface": "surface_m2", "unite_fonciere_surface": "surface_m2", "surface": "surface_m2", "surface_m²": "surface_m2",
    "site_type": "type_friche", "type": "type_friche",
    "site_statut": "statut_cartofriches",
    "bati_surface": "bati_emprise_m2",
    "activite_libelle": "activite_passee",
    "urba_zone_type": "plu_zone",
    "proprio_nombre": "nb_proprietaires",
    "proprio_personne": "proprietaire_type",
}

TYPE_MAP = {
    "friche industrielle": "industrielle", "friche d'activite": "industrielle", "friche commerciale": "commerciale",
    "friche ferroviaire": "ferroviaire", "friche portuaire": "portuaire", "friche militaire": "militaire",
    "friche hospitaliere": "hospitaliere", "friche d'habitat": "habitat", "friche tertiaire": "tertiaire",
    "friche carriere ou mine": "carriere", "friche agro-industrielle": "industrielle", "friche logistique": "logistique",
    "friche cultuelle": "inconnu", "friche enseignement": "inconnu", "friche loisir tourisme hotellerie": "commerciale",
    "mixte": "inconnu", "industriel": "industrielle", "commerce": "commerciale", "hopital": "hospitaliere",
    "caserne": "militaire", "bureaux": "tertiaire", "mine": "carriere",
}

ENUM_SYNONYMS = {
    "bati_etat": {"bon etat": "bon", "degrade": "degrade", "tres degrade": "ruine", "en ruine": "ruine", "mauvais": "degrade"},
    "zone_humide": {"averee": "avere", "oui": "avere", "probable": "probable", "non": "non", "aucune": "non"},
    "captage": {"immediate": "immediat", "rapprochee": "rapproche", "eloignee": "eloigne", "aucun": "non", "non": "non"},
    "ppri": {"aucun": "non", "hors zone": "non", "non": "non"},
    "statut_cartofriches": {"verifiee": "verifiee", "verifie": "verifiee", "non verifiee": "non_verifiee",
                            "friche avec projet": "verifiee", "friche sans projet": "verifiee", "friche potentielle": "non_verifiee"},
    "pollution_constatee": {"aucune": "aucune", "non": "aucune", "moderee": "moderee", "faible": "moderee", "forte": "forte", "importante": "forte"},
    "proprietaire_type": {"publique": "public", "privee": "prive", "personne physique": "prive",
                          "personne physique, personne morale": "mixte", "personne morale, personne physique": "mixte"},
    "desserte_tc": {"bonne": "forte", "mauvaise": "faible"},
    "demande_activite": {"elevee": "forte"},
    "deficit_equipements": {"eleve": "fort"},
}

# Colonnes secondaires : utilisées seulement si la colonne principale de même sens est absente
SECONDARY = {"unite_fonciere_surface", "x", "y", "type"}

CONFIANCES = {"eleve": "eleve", "elevee": "eleve", "haute": "eleve", "moyen": "moyen", "moyenne": "moyen",
              "faible": "faible", "basse": "faible"}
TRUE = {"1", "true", "oui", "vrai", "yes", "o", "x", "y"}
FALSE = {"0", "false", "non", "faux", "no", "n", ""}

# bornes de vraisemblance : (min, max, avertissement seulement ?)
BOUNDS = {
    "lat": (41.0, 51.5, True), "lon": (-5.5, 10.0, True), "surface_m2": (1, 50_000_000, False),
    "bati_emprise_m2": (0, 50_000_000, False), "bati_annee": (1500, date.today().year, False),
    "nb_proprietaires": (0, 10_000, False), "basias_count": (0, 500, False), "icpe_count": (0, 500, False),
    "prix_m2_logement": (100, 30_000, True), "irradiation_kwh_m2": (700, 2_200, True),
    "vacance_logement_pct": (0, 100, False), "vacance_tertiaire_pct": (0, 100, False),
    "vacance_locaux_activite_pct": (0, 100, False),
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s) -> str:
    return re.sub(r"\s+", " ", strip_accents(str(s)).strip().lower())


_LABEL_TO_KEY = {norm(v[0]): k for k, v in CATALOG.items()}


def resolve_column(col: str) -> str | None:
    """Nom de colonne -> clé FricheScore (clé exacte, alias, ou libellé du catalogue)."""
    c = str(col).strip().lstrip("﻿")
    if c in CATALOG or c == "id":
        return c
    n = norm(c)
    if n in CATALOG or n == "id":
        return n
    n2 = n.replace(" ", "_")
    if n2 in CATALOG:
        return n2
    return ALIASES.get(n) or ALIASES.get(n2) or _LABEL_TO_KEY.get(n)


def parse_number(raw: str) -> float:
    s = str(raw).strip().replace(" ", "").replace(" ", "").replace(" ", "")
    s = re.sub(r"(m²|m2|€|eur|ha|%|km)$", "", s, flags=re.I)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")  # 12.000,5 -> 12000.5
    else:
        s = s.replace(",", ".")
    return float(s)


def parse_cell(key: str, raw) -> tuple[object, str | None, str | None]:
    """Retourne (valeur, erreur, avertissement). Valeur None = cellule ignorée."""
    if raw is None:
        return None, None, None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "" or raw.lower() in ("na", "n/a", "nr", "n.r.", "null", "none", "-"):
            return None, None, None
    if key == "id":
        return str(raw), None, None
    if key == "code_insee":
        s = str(raw).strip().upper()
        if re.fullmatch(r"\d{4}", s):
            return "0" + s, None, f"code INSEE « {raw} » complété en « 0{s} » (zéro initial perdu par le tableur ?)"
        if re.fullmatch(r"(\d{5}|2[AB]\d{3}|97\d{3})", s):
            return s, None, None
        return None, f"code INSEE « {raw} » invalide (5 caractères attendus, ex. 59350 ou 2A004)", None
    typ = CATALOG.get(key, (None, "str"))[1]
    allowed = CATALOG.get(key, (None, None, None, None, None))[4]
    if typ in ("float", "int"):
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            v = float(raw)
        else:
            try:
                v = parse_number(raw)
            except ValueError:
                return None, f"« {raw} » n'est pas un nombre", None
        b = BOUNDS.get(key)
        if b and not (b[0] <= v <= b[1]):
            msg = f"valeur {v:g} hors de la plage plausible [{b[0]:g} ; {b[1]:g}]"
            if b[2]:
                return (int(v) if typ == "int" else v), None, msg
            return None, msg, None
        if typ == "int":
            if not float(v).is_integer():
                return int(round(v)), None, f"« {raw} » arrondi à {int(round(v))}"
            return int(v), None, None
        return v, None, None
    if typ == "bool":
        if isinstance(raw, bool):
            return raw, None, None
        n = norm(raw)
        if n in TRUE:
            return True, None, None
        if n in FALSE:
            return False, None, None
        return None, f"« {raw} » n'est pas reconnu comme oui/non", None
    if typ == "list":
        if isinstance(raw, list):
            return raw, None, None
        return [s.strip() for s in re.split(r"[|;,]", str(raw)) if s.strip()], None, None
    if typ == "enum":
        n = norm(raw)
        if key == "type_friche":
            if n in allowed:
                return n, None, None
            if n in TYPE_MAP:
                return TYPE_MAP[n], None, None
            return "inconnu", None, f"type « {raw} » non reconnu, enregistré comme « inconnu »"
        if key == "plu_zone":
            s = re.sub(r"^\d+", "", str(raw).strip().upper())  # 1AUh, 2AU -> AU...
            for z in ("AU", "U", "A", "N"):
                if s.startswith(z):
                    return z, None, (None if s == z else f"zone « {raw} » ramenée à « {z} »")
            return None, f"zone PLU « {raw} » non reconnue (U, AU, A ou N attendu)", None
        cand = n.replace(" ", "_")
        if cand in allowed:
            return cand, None, None
        syn = ENUM_SYNONYMS.get(key, {})
        if n in syn:
            return syn[n], None, None
        return None, f"valeur « {raw} » non reconnue (attendu : {', '.join(allowed)})", None
    return str(raw), None, None


PARTICULES = {"de", "du", "des", "la", "le", "les", "sur", "sous", "en", "et", "lès", "lez", "aux", "au", "d", "l"}


def nom_commune(v: str) -> str:
    """« VILLENEUVE-D'ASCQ » -> « Villeneuve-d'Ascq » ; ne touche pas un nom déjà en casse mixte."""
    if not v or not v.isupper():
        return v
    out, word = [], ""
    seps = " -'’"
    for ch in v.lower() + " ":
        if ch in seps:
            if word:
                out.append(word if (word in PARTICULES and out) else word[:1].upper() + word[1:])
                word = ""
            out.append(ch)
        else:
            word += ch
    return "".join(out).strip()


def row_to_record(row: dict, origine_label: str, default_date: str | None = None,
                  default_conf: str | None = None) -> tuple[Record, list[str]]:
    """Version simple (connecteurs) : ignore les problèmes de cellules."""
    rec, ignored, _issues = validate_row(row, origine_label, default_date, default_conf)
    return rec, ignored


def validate_row(row: dict, origine_label: str, default_date: str | None = None,
                 default_conf: str | None = None) -> tuple[Record, list[str], list[dict]]:
    """Convertit une ligne en Record tracé. Retourne (record, colonnes ignorées, problèmes).
    Un problème = {colonne, valeur, niveau: erreur|avertissement, message}."""
    rec = Record()
    ignored, issues = [], []
    today = default_date or date.today().isoformat()
    cols = {str(k).strip().lstrip("﻿"): v for k, v in row.items() if k is not None}
    for col, raw in cols.items():
        base = col.split("__")[0]
        if col.endswith(("__source", "__date", "__confiance")):
            continue
        key = resolve_column(col)
        if key is None:
            ignored.append(col)
            continue
        if norm(col) in SECONDARY and any(resolve_column(o) == key and norm(o) not in SECONDARY for o in cols):
            continue
        val, err, warn = parse_cell(key, raw)
        if err:
            issues.append({"colonne": col, "valeur": str(raw)[:60], "niveau": "erreur", "message": err + " — cellule ignorée"})
            continue
        if warn:
            issues.append({"colonne": col, "valeur": str(raw)[:60], "niveau": "avertissement", "message": warn})
        if val is None:
            continue
        if key == "commune" and isinstance(val, str):
            val = nom_commune(val)
        src = cols.get(f"{base}__source") or (f"{CATALOG[key][2]} via {origine_label}" if key in CATALOG else origine_label)
        conf_raw = cols.get(f"{base}__confiance")
        conf = None
        if conf_raw not in (None, ""):
            conf = CONFIANCES.get(norm(conf_raw))
            if conf is None:
                issues.append({"colonne": f"{base}__confiance", "valeur": str(conf_raw), "niveau": "avertissement",
                               "message": "confiance non reconnue (élevé, moyen, faible) — confiance par défaut appliquée"})
        if conf is None:
            conf = "faible" if key == "basias_count" else (default_conf or default_confidence(key))
        d = cols.get(f"{base}__date") or today
        rec.set(key, val, Provenance(src, str(d), conf))
    if rec.has("code_insee") and not rec.has("departement"):
        ci = str(rec.get("code_insee"))
        rec.values["departement"] = ci[:3] if ci.startswith("97") else ci[:2]
    return rec, ignored, issues
