"""Dimension 3 — Friction réglementaire et procédurale.

Score de friction 0-100 : PLUS IL EST ÉLEVÉ, PLUS LE SITE EST PÉNALISÉ.
Le global utilise (100 - friction). Chaque facteur apporte des points de
friction et une estimation de délai additionnel en mois. Les procédures
pouvant être menées en parallèle, le délai cumulé = max + 50 % du reste.
"""
from __future__ import annotations

from . import config as C
from .record import Tracker

# Compatibilité zonage PLU / usage : 0 = compatible, 1 = modification, 2 = révision / mise en compatibilité lourde
_PLU_COMPAT = {
    "logement": {"U": {"habitat": 0, "mixte": 0, "activite": 1, "equipement": 1}, "AU": 1, "A": 2, "N": 2},
    "tertiaire": {"U": {"habitat": 0, "mixte": 0, "activite": 0, "equipement": 1}, "AU": 1, "A": 2, "N": 2},
    "activite": {"U": {"habitat": 1, "mixte": 0, "activite": 0, "equipement": 1}, "AU": 1, "A": 2, "N": 2},
    "equipement": {"U": {"habitat": 0, "mixte": 0, "activite": 0, "equipement": 0}, "AU": 1, "A": 2, "N": 1},
    "photovoltaique": {"U": {"habitat": 1, "mixte": 1, "activite": 0, "equipement": 1}, "AU": 1, "A": 1, "N": 1},
    "renaturation": {"U": {"habitat": 0, "mixte": 0, "activite": 0, "equipement": 0}, "AU": 0, "A": 0, "N": 0},
}


def compute_friction(t: Tracker, usage: str, hyp: dict | None = None) -> dict:
    hyp = hyp or {}
    items: list[dict] = []

    def f(label, pts, mois, key):
        items.append({"label": label, "points": pts, "mois": mois, "key": key})

    zh = t.read("zone_humide", None, 1.5)
    if usage != "renaturation":
        if zh == "avere":
            f("Zone humide avérée : dossier loi sur l'eau et compensation", 25, 12, "zone_humide")
        elif zh == "probable":
            f("Zone humide probable : délimitation pédologique à réaliser", 12, 6, "zone_humide")
        elif zh is None:
            f("Présence de zone humide non vérifiée", 4, 2, "zone_humide")

    cap = t.read("captage", "non", 1)
    if cap == "immediat":
        f("Périmètre de protection immédiate de captage", 40, 24, "captage")
    elif cap == "rapproche":
        f("Périmètre de protection rapprochée de captage", 20, 6, "captage")
    elif cap == "eloigne":
        f("Périmètre de protection éloignée de captage", 6, 2, "captage")

    ppri = t.read("ppri", "non", 1)
    if usage != "renaturation":
        if ppri == "rouge":
            f("Zone rouge du PPRi", 30, 6, "ppri")
        elif ppri == "bleu":
            f("Zone bleue du PPRi (prescriptions)", 10, 3, "ppri")
    if t.read("pprt", False, 0.5) and usage != "renaturation":
        f("Périmètre de PPRT", 15, 6, "pprt")
    if t.read("natura2000", False, 1):
        f("Site Natura 2000 : évaluation des incidences", 15 if usage != "renaturation" else 3, 6, "natura2000")
    elif t.read("znieff", False, 0.5) and usage != "renaturation":
        f("ZNIEFF : inventaires faune-flore 4 saisons", 6, 4, "znieff")
    if t.read("abf", False, 0.5):
        f("Périmètre de monument historique (avis ABF)", 8, 3, "abf")
    if t.read("loi_littoral", False, 0.3):
        f("Commune littorale (loi Littoral)", 10, 6, "loi_littoral")

    # Évaluation environnementale (annexe R.122-2 du code de l'environnement)
    surf = float(t.read("surface_m2", 0, 1))
    if usage == "photovoltaique":
        mwc = surf / 10_000 * 0.9
        if mwc >= 1:
            f(f"Étude d'impact systématique (centrale ≈ {mwc:.0f} MWc)", 12, 10, "surface_m2")
        elif mwc >= 0.3:
            f("Examen au cas par cas (centrale 0,3-1 MWc)", 5, 3, "surface_m2")
    elif usage != "renaturation":
        if surf >= 100_000:
            f("Étude d'impact systématique (terrain d'assiette ≥ 10 ha)", 15, 12, "surface_m2")
        elif surf >= 50_000:
            f("Examen au cas par cas (terrain d'assiette 5-10 ha)", 6, 3, "surface_m2")

    zone = t.read("plu_zone", None, 1.5)
    voc = t.read("plu_vocation", "mixte", 1)
    if zone is None or zone == "inconnu":
        f("Zonage PLU(i) non renseigné", 8, 6, "plu_zone")
    else:
        rule = _PLU_COMPAT[usage].get(zone, 1)
        lvl = rule.get(voc, 1) if isinstance(rule, dict) else rule
        if lvl == 1:
            f(f"PLU à faire évoluer (zone {zone}, vocation {voc}) : modification", 10, 10, "plu_zone")
        elif lvl == 2:
            f(f"PLU incompatible (zone {zone}) : révision ou déclaration de projet", 25, 24, "plu_zone")

    if t.rec.get("site_extension") and usage != "renaturation":
        f("Ouverture à l'urbanisation sous contrainte ZAN (justification de l'absence d'alternative)", 12, 6, "site_extension")

    nserv = int(t.read("servitudes_count", 0, 0.5) or 0)
    if nserv:
        f(f"{nserv} servitude(s) d'utilité publique", min(12, 3 * nserv), min(6, 2 * nserv), "servitudes_count")

    icpe_statut = t.read("icpe_statut", None, 1)
    if icpe_statut == "en_activite":
        f("ICPE en activité : procédure de cessation et attestations à mener", 15, 12, "icpe_statut")
    elif icpe_statut == "cessee" and t.rec.get("icpe_regime") in ("autorisation", "enregistrement"):
        f("ICPE cessée : vérifier le procès-verbal de récolement", 6, 4, "icpe_statut")
    if t.read("seveso", None, 0.3):
        f("Ancien site SEVESO : servitudes et contrôle renforcé", 10, 6, "seveso")

    nb_p = t.read("nb_proprietaires", None, 1)
    if nb_p is not None:
        if nb_p > 5:
            f(f"{nb_p} propriétaires : acquisition amiable incertaine (DUP possible)", 12, 12, "nb_proprietaires")
        elif nb_p >= 3:
            f(f"{nb_p} propriétaires à négocier", 6, 6, "nb_proprietaires")
    elif t.rec.get("proprietaire_type") != "public":
        f("Structure de propriété inconnue", 4, 3, "nb_proprietaires")

    extra = float(hyp.get("delai_supplementaire_mois", 0))
    if extra:
        f(f"Hypothèse : délais allongés de {extra:.0f} mois", extra * 1.2, extra, None)

    pts = min(100.0, sum(i["points"] for i in items))
    months = sorted((i["mois"] for i in items), reverse=True)
    add_months = (months[0] + 0.5 * sum(months[1:])) if months else 0.0
    for i in items:
        t.add(i["label"], -i["points"], i["key"])

    top = sorted(items, key=lambda x: -x["points"])[:2]
    if pts >= 50:
        lvl = "forte"
    elif pts >= 25:
        lvl = "notable"
    elif pts >= 10:
        lvl = "modérée"
    else:
        lvl = "faible"
    phrase = f"Friction {lvl}" + (" : " + " ; ".join(i["label"].lower() for i in top) + "." if top else " : aucune contrainte identifiée.")
    return {
        "friction": round(pts, 1),
        "niveau": lvl,
        "phrase": phrase,
        "delai_additionnel_mois": round(add_months),
        "delai_total_mois": round(C.BASE_DELAY_MONTHS + add_months),
        "facteurs": items,
    }
