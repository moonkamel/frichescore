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

    F = C.FRICTION

    def f(label, code, key):
        """Ajoute une contrainte. `code` renvoie aux valeurs paramétrables de config.FRICTION ;
        un tuple (points, mois) est accepté pour les hypothèses de contre-analyse."""
        pts, mois = F[code] if isinstance(code, str) else code
        items.append({"label": label, "points": pts, "mois": mois, "key": key})

    zh = t.read("zone_humide", None, 1.5)
    if usage != "renaturation":
        if zh == "avere":
            f("Zone humide avérée : dossier loi sur l'eau et compensation", "zone_humide_avere", "zone_humide")
        elif zh == "probable":
            f("Zone humide probable : délimitation pédologique à réaliser", "zone_humide_probable", "zone_humide")
        elif zh is None:
            f("Présence de zone humide non vérifiée", "zone_humide_non_verifiee", "zone_humide")

    cap = t.read("captage", "non", 1)
    if cap == "immediat":
        f("Périmètre de protection immédiate de captage", "captage_immediat", "captage")
    elif cap == "rapproche":
        f("Périmètre de protection rapprochée de captage", "captage_rapproche", "captage")
    elif cap == "eloigne":
        f("Périmètre de protection éloignée de captage", "captage_eloigne", "captage")

    ppri = t.read("ppri", "non", 1)
    if usage != "renaturation":
        if ppri == "rouge":
            f("Zone rouge du PPRi", "ppri_rouge", "ppri")
        elif ppri == "bleu":
            f("Zone bleue du PPRi (prescriptions)", "ppri_bleu", "ppri")
    if usage != "renaturation" and t.rec.get("risque_inondation") and not t.rec.has("ppri"):
        f("Risque d'inondation recensé (Géorisques) : zonage du PPRi à vérifier", "risque_inondation_sans_ppri", "risque_inondation")
    if t.read("pprt", False, 0.5) and usage != "renaturation":
        f("Périmètre de PPRT", "pprt", "pprt")
    if t.read("natura2000", False, 1):
        f("Site Natura 2000 : évaluation des incidences", "natura2000" if usage != "renaturation" else "natura2000_renaturation", "natura2000")
    elif t.read("znieff", False, 0.5) and usage != "renaturation":
        f("ZNIEFF : inventaires faune-flore 4 saisons", "znieff", "znieff")
    if t.read("abf", False, 0.5):
        f("Périmètre de monument historique (avis ABF)", "abf", "abf")
    if t.read("loi_littoral", False, 0.3):
        f("Commune littorale (loi Littoral)", "loi_littoral", "loi_littoral")

    # Évaluation environnementale (annexe R.122-2 du code de l'environnement)
    surf = float(t.read("surface_m2", 0, 1))
    if usage == "photovoltaique":
        mwc = surf / 10_000 * 0.9
        if mwc >= C.SEUIL_PV_ETUDE_IMPACT_MWC:
            f(f"Étude d'impact systématique (centrale ≈ {mwc:.0f} MWc)", "pv_etude_impact_systematique", "surface_m2")
        elif mwc >= C.SEUIL_PV_CAS_PAR_CAS_MWC:
            f("Examen au cas par cas (petite centrale)", "pv_cas_par_cas", "surface_m2")
    elif usage != "renaturation":
        if surf >= C.SEUIL_ETUDE_IMPACT_HA * 10_000:
            f(f"Étude d'impact systématique (terrain d'assiette ≥ {C.SEUIL_ETUDE_IMPACT_HA:g} ha)", "etude_impact_systematique", "surface_m2")
        elif surf >= C.SEUIL_CAS_PAR_CAS_HA * 10_000:
            f(f"Examen au cas par cas (terrain d'assiette ≥ {C.SEUIL_CAS_PAR_CAS_HA:g} ha)", "etude_impact_cas_par_cas", "surface_m2")

    zone = t.read("plu_zone", None, 1.5)
    voc = t.read("plu_vocation", "mixte", 1)
    if zone is None or zone == "inconnu":
        f("Zonage PLU(i) non renseigné", "plu_non_renseigne", "plu_zone")
    else:
        rule = _PLU_COMPAT[usage].get(zone, 1)
        lvl = rule.get(voc, 1) if isinstance(rule, dict) else rule
        if lvl == 1:
            f(f"PLU à faire évoluer (zone {zone}, vocation {voc}) : modification", "plu_modification", "plu_zone")
        elif lvl == 2:
            f(f"PLU incompatible (zone {zone}) : révision ou déclaration de projet", "plu_revision", "plu_zone")

    if t.rec.get("site_extension") and usage != "renaturation":
        f("Ouverture à l'urbanisation sous contrainte ZAN (justification de l'absence d'alternative)", "extension_zan", "site_extension")

    nserv = int(t.read("servitudes_count", 0, 0.5) or 0)
    if nserv:
        sp, sm = F["servitude_par_unite"]
        f(f"{nserv} servitude(s) d'utilité publique", (min(4 * sp, sp * nserv), min(3 * sm, sm * nserv)), "servitudes_count")

    icpe_statut = t.read("icpe_statut", None, 1)
    if icpe_statut == "en_activite":
        f("ICPE en activité : procédure de cessation et attestations à mener", "icpe_en_activite", "icpe_statut")
    elif icpe_statut == "cessee" and t.rec.get("icpe_regime") in ("autorisation", "enregistrement"):
        f("ICPE cessée : vérifier le procès-verbal de récolement", "icpe_cessee", "icpe_statut")
    if t.read("seveso", None, 0.3):
        f("Ancien site SEVESO : servitudes et contrôle renforcé", "seveso", "seveso")

    nb_p = t.read("nb_proprietaires", None, 1)
    if nb_p is not None:
        if nb_p > 5:
            f(f"{nb_p} propriétaires : acquisition amiable incertaine (DUP possible)", "proprietaires_plus_de_5", "nb_proprietaires")
        elif nb_p >= 3:
            f(f"{nb_p} propriétaires à négocier", "proprietaires_3_a_5", "nb_proprietaires")
    elif t.rec.get("proprietaire_type") != "public":
        f("Structure de propriété inconnue", "proprietaires_inconnus", "nb_proprietaires")

    comp = hyp.get("compensation_zone_humide")
    if comp in ("tous", "concernes") and usage != "renaturation":
        if comp == "tous" or t.rec.get("zone_humide") in ("avere", "probable"):
            f("Hypothèse : création d'une zone humide compensatoire exigée", "compensation_zone_humide", "zone_humide")

    extra = float(hyp.get("delai_supplementaire_mois", 0))
    if extra:
        f(f"Hypothèse : délais allongés de {extra:.0f} mois", (extra * C.FRICTION_POINTS_PAR_MOIS_HYPOTHESE, extra), None)

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
