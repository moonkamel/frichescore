"""Paramètres modifiables sans toucher au code.

Les valeurs par défaut restent dans `config.py` (et `record.py` pour la
confiance). Les surcharges de l'utilisateur sont stockées en base et
appliquées EN PLACE sur ces modules : comme le moteur lit `C.COST[...]`,
`C.FRICTION[...]`, etc. au moment du calcul, une modification prend effet
immédiatement sur tous les scores, sans redémarrage.

Chaque paramètre est désigné par un chemin, ex. `COST.pollution.basol_eaux`
ou `FRICTION.zone_humide_avere`, et décrit par un schéma (libellé, unité,
composantes, bornes) qui sert à la page « Paramètres » et à la validation.
"""
from __future__ import annotations

import copy

from . import config as C
from . import record as R

# Composantes des valeurs composées
RANGE = ["bas", "haut"]
POLLUTION = ["coût bas €/m²", "coût haut €/m²", "part du site (bas)", "part du site (haute)"]
FRICTION = ["points", "mois"]

USAGE_LBL = C.USAGES

POLLUTION_LBL = {
    "basol_eaux": "BASOL — sols et eaux souterraines",
    "basol_sols": "BASOL — sols seuls",
    "sis": "Secteur d'information sur les sols (SIS)",
    "icpe_seveso": "ICPE SEVESO",
    "icpe_cessee": "ICPE autorisation / enregistrement",
    "basias": "BASIAS — ancien site industriel non diagnostiqué",
    "terrain_forte": "Diagnostic : pollution forte",
    "terrain_moderee": "Diagnostic : pollution modérée",
    "terrain_aucune": "Diagnostic : pas de pollution",
    "inconnue": "Pollution non renseignée",
}

FRICTION_LBL = {
    "zone_humide_avere": "Zone humide avérée", "zone_humide_probable": "Zone humide probable",
    "zone_humide_non_verifiee": "Zone humide non vérifiée", "captage_immediat": "Captage — protection immédiate",
    "captage_rapproche": "Captage — protection rapprochée", "captage_eloigne": "Captage — protection éloignée",
    "ppri_rouge": "PPRi zone rouge", "ppri_bleu": "PPRi zone bleue", "pprt": "Périmètre PPRT",
    "natura2000": "Natura 2000", "natura2000_renaturation": "Natura 2000 (projet de renaturation)",
    "znieff": "ZNIEFF", "abf": "Abords de monument historique (ABF)", "loi_littoral": "Loi Littoral",
    "etude_impact_systematique": "Étude d'impact systématique", "etude_impact_cas_par_cas": "Examen au cas par cas",
    "pv_etude_impact_systematique": "PV : étude d'impact systématique", "pv_cas_par_cas": "PV : examen au cas par cas",
    "plu_non_renseigne": "PLU(i) non renseigné", "plu_modification": "PLU(i) : modification nécessaire",
    "plu_revision": "PLU(i) : révision / déclaration de projet", "servitude_par_unite": "Par servitude d'utilité publique",
    "icpe_en_activite": "ICPE en activité (cessation à mener)", "icpe_cessee": "ICPE cessée (récolement à vérifier)",
    "seveso": "Ancien site SEVESO", "proprietaires_plus_de_5": "Plus de 5 propriétaires",
    "proprietaires_3_a_5": "3 à 5 propriétaires", "proprietaires_inconnus": "Propriété inconnue",
    "extension_zan": "Site en extension : justification ZAN",
}


def _schema() -> list[dict]:
    s: list[dict] = []

    def add(path, groupe, label, comps=None, unite="", aide="", mini=0.0, maxi=None):
        s.append({"path": path, "groupe": groupe, "label": label, "composantes": comps, "unite": unite,
                  "aide": aide, "min": mini, "max": maxi})

    g = "Coûts unitaires de remise en état"
    add("COST.demolition_m2_emprise", g, "Démolition et curage", RANGE, "€/m² d'emprise bâtie")
    add("COST.desamiantage_m2_emprise", g, "Désamiantage", RANGE, "€/m² d'emprise bâtie", "Appliqué au bâti antérieur à 1997")
    add("COST.desimpermeabilisation_m2", g, "Désimperméabilisation (renaturation)", RANGE, "€/m² imperméabilisé")
    add("COST.foncier_multi_proprio_m2", g, "Surcoût de maîtrise foncière (≥ 3 propriétaires)", RANGE, "€/m² de terrain")
    add("COST.raccordement_pv_eur_km", g, "Raccordement photovoltaïque", RANGE, "€/km jusqu'au poste source")
    add("COST.extension_reseaux_m2", g, "Extension des réseaux (site en extension)", RANGE, "€/m² de terrain")
    g = "Viabilisation / VRD par usage"
    for u in C.COST["viabilisation"]:
        add(f"COST.viabilisation.{u}", g, USAGE_LBL[u], RANGE, "€/m² de terrain")
    g = "Dépollution par signal de pollution"
    for k in C.COST["pollution"]:
        add(f"COST.pollution.{k}", g, POLLUTION_LBL.get(k, k), POLLUTION, "",
            "Coût = surface × coût unitaire × part du site concernée × sensibilité de l'usage")
    g = "Sensibilité de l'usage à la pollution"
    for u in C.USAGE_POLLUTION_SENSITIVITY:
        add(f"USAGE_POLLUTION_SENSITIVITY.{u}", g, USAGE_LBL[u], None, "× coût de dépollution", maxi=3)
    g = "Friction réglementaire (pénalité et délai)"
    for k in C.FRICTION:
        add(f"FRICTION.{k}", g, FRICTION_LBL.get(k, k), FRICTION)
    g = "Délais et seuils réglementaires"
    add("FRICTION_POINTS_PAR_MOIS_HYPOTHESE", g, "Contre-analyse : points de friction par mois ajouté", None, "pts/mois")
    add("BASE_DELAY_MONTHS", g, "Délai de base sans contrainte", None, "mois", "Études pré-opérationnelles + autorisations")
    add("SEUIL_ETUDE_IMPACT_HA", g, "Seuil d'étude d'impact systématique", None, "ha")
    add("SEUIL_CAS_PAR_CAS_HA", g, "Seuil d'examen au cas par cas", None, "ha")
    add("SEUIL_PV_ETUDE_IMPACT_MWC", g, "PV : seuil d'étude d'impact", None, "MWc")
    add("SEUIL_PV_CAS_PAR_CAS_MWC", g, "PV : seuil de cas par cas", None, "MWc")
    g = "Score global et confiance"
    add("CONFIDENCE_MAX_PENALTY", g, "Pénalité maximale de confiance", None, "points", maxi=50)
    add("BLOCKER_CAP", g, "Plafond d'un usage rédhibitoire", None, "points", maxi=100)
    add("COST_SCALE_EUR_M2", g, "Coût au-delà duquel le risque de coût est maximal", None, "€/m²", mini=1)
    add("UNCERTAINTY_SCALE_EUR_M2", g, "Largeur de fourchette au-delà de laquelle l'incertitude est maximale", None, "€/m²", mini=1)
    add("R.CONFIDENCE_WEIGHT.eleve", g, "Poids d'une source de confiance élevée", None, "0-1", maxi=1)
    add("R.CONFIDENCE_WEIGHT.moyen", g, "Poids d'une source de confiance moyenne", None, "0-1", maxi=1)
    add("R.CONFIDENCE_WEIGHT.faible", g, "Poids d'une source de confiance faible", None, "0-1", maxi=1)
    add("R.MISSING_WEIGHT", g, "Poids d'un indicateur manquant", None, "0-1", maxi=1)
    g = "Seuils de qualification (coûts cachés)"
    add("PIEGEUX_D2_MAX", g, "Site « piégeux » si note de coût inférieure à", None, "note D2", maxi=100)
    add("PEPITE_D2_MIN", g, "Pépite : note de coût au moins", None, "note D2", maxi=100)
    add("PEPITE_FRICTION_MAX", g, "Pépite : friction au plus", None, "points", maxi=100)
    add("PEPITE_CONF_MIN", g, "Pépite : confiance au moins", None, "0-1", maxi=1)
    g = "Profils de pondération"
    for p, v in C.PROFILES.items():
        for d in v["weights"]:
            add(f"PROFILES.{p}.weights.{d}", g, f"{v['label']} — {C.DIMENSIONS[d]}", None, "poids", maxi=100)
    return s


SCHEMA = _schema()
SCHEMA_BY_PATH = {x["path"]: x for x in SCHEMA}

# Instantané des valeurs par défaut, pris à l'import (avant toute surcharge)
_DEFAULTS: dict = {}


def _resolve(path: str):
    """Retourne (conteneur, clé) pour un chemin donné."""
    parts = path.split(".")
    if parts[0] == "R":
        obj, parts = R, parts[1:]
    else:
        obj = C
    head = parts[0]
    if len(parts) == 1:
        return obj, head, True
    cont = getattr(obj, head)
    for p in parts[1:-1]:
        cont = cont[p]
    return cont, parts[-1], False


def get_value(path: str):
    cont, key, is_attr = _resolve(path)
    v = getattr(cont, key) if is_attr else cont[key]
    return list(v) if isinstance(v, tuple) else v


def _set_value(path: str, value):
    cont, key, is_attr = _resolve(path)
    current = getattr(cont, key) if is_attr else cont[key]
    if isinstance(current, tuple):
        value = tuple(value)
    if is_attr:
        setattr(cont, key, value)
    else:
        cont[key] = value


for _x in SCHEMA:
    _DEFAULTS[_x["path"]] = copy.deepcopy(get_value(_x["path"]))


def default(path: str):
    return copy.deepcopy(_DEFAULTS[path])


def validate(path: str, value) -> tuple[object, str | None]:
    """Contrôle une valeur ; retourne (valeur normalisée, erreur éventuelle)."""
    sch = SCHEMA_BY_PATH.get(path)
    if sch is None:
        return None, "paramètre inconnu"
    comps = sch["composantes"]
    vals = value if isinstance(value, (list, tuple)) else [value]
    try:
        vals = [float(str(v).replace(",", ".")) for v in vals]
    except (TypeError, ValueError):
        return None, "valeur non numérique"
    expected = len(comps) if comps else 1
    if len(vals) != expected:
        return None, f"{expected} valeur(s) attendue(s)"
    for v in vals:
        if sch["min"] is not None and v < sch["min"]:
            return None, f"valeur inférieure au minimum ({sch['min']:g})"
        if sch["max"] is not None and v > sch["max"]:
            return None, f"valeur supérieure au maximum ({sch['max']:g})"
    if comps in (RANGE, POLLUTION) and vals[0] > vals[1]:
        return None, "la valeur basse dépasse la valeur haute"
    if comps == POLLUTION:
        if vals[2] > vals[3]:
            return None, "la part basse dépasse la part haute"
        if vals[3] > 1:
            return None, "une part du site ne peut pas dépasser 1"
    if comps is None:
        d = _DEFAULTS[path]
        return (int(vals[0]) if isinstance(d, int) and float(vals[0]).is_integer() else vals[0]), None
    return vals, None


def apply(overrides: dict[str, object]) -> list[str]:
    """Remet tous les paramètres à leur valeur par défaut puis applique les
    surcharges valides. Retourne les erreurs (surcharges ignorées)."""
    errors = []
    for path in SCHEMA_BY_PATH:
        _set_value(path, copy.deepcopy(_DEFAULTS[path]))
    for path, v in overrides.items():
        val, err = validate(path, v)
        if err:
            errors.append(f"{path} : {err}")
            continue
        _set_value(path, val)
    return errors


def snapshot(overrides: dict[str, dict]) -> list[dict]:
    """Vue complète pour l'interface : schéma + valeur courante + défaut + note."""
    out = []
    for sch in SCHEMA:
        p = sch["path"]
        ov = overrides.get(p)
        out.append({**sch, "valeur": get_value(p), "defaut": default(p), "modifie": ov is not None,
                    "note": (ov or {}).get("note"), "updated_at": (ov or {}).get("updated_at")})
    return out
