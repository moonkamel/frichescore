"""Dimension 2 — Coût de remise en état (indice de risque de surcoût).

Ce n'est PAS un devis. Le module produit :
  * une fourchette de coût (bas / haut, en € et en €/m² de terrain),
  * un indice de risque de surcoût (niveau de coût + largeur de l'incertitude),
  * une phrase d'explication lisible en comité.

Score D2 : 100 = remise en état peu coûteuse et bien cernée ; 0 = coût élevé
et/ou très incertain.
"""
from __future__ import annotations

from . import config as C
from .record import Tracker


def _pollution_signals(t: Tracker, pessimiste: bool) -> list[dict]:
    """Liste des signaux de pollution, du plus fiable au moins fiable.
    Une donnée terrain (diagnostic de sol) prime sur les bases nationales."""
    P = C.COST["pollution"]
    rec = t.rec
    terrain = t.read("pollution_constatee", None, 2)
    if terrain:
        key = {"forte": "terrain_forte", "moderee": "terrain_moderee", "aucune": "terrain_aucune"}[terrain]
        lbl = {"forte": "pollution forte confirmée par diagnostic",
               "moderee": "pollution modérée confirmée par diagnostic",
               "aucune": "diagnostic de sol rassurant"}[terrain]
        return [{"key": "pollution_constatee", "label": lbl, "p": P[key], "poids": 1.0}]

    sig = []
    basol = t.read("basol", None, 2)
    if basol:
        milieux = rec.get("basol_milieux", []) or []
        if "eaux_souterraines" in milieux:
            sig.append({"key": "basol", "label": "pollution BASOL sols et eaux souterraines", "p": P["basol_eaux"], "poids": 1.0})
        else:
            sig.append({"key": "basol", "label": "pollution BASOL des sols", "p": P["basol_sols"], "poids": 1.0})
    if t.read("sis", None, 1):
        sig.append({"key": "sis", "label": "secteur d'information sur les sols (SIS)", "p": P["sis"], "poids": 1.0})
    seveso = t.read("seveso", None, 0.5)
    icpe_n = t.read("icpe_count", None, 1)
    if seveso:
        sig.append({"key": "seveso", "label": f"établissement SEVESO seuil {seveso}", "p": P["icpe_seveso"], "poids": 1.0})
    elif icpe_n and rec.get("icpe_regime") in ("autorisation", "enregistrement"):
        sig.append({"key": "icpe_count", "label": f"ICPE soumise à {rec.get('icpe_regime')}", "p": P["icpe_cessee"], "poids": 1.0})
    basias_n = t.read("basias_count", None, 1)
    if basias_n:
        sig.append({"key": "basias_count", "label": f"{basias_n} ancien(s) site(s) BASIAS non diagnostiqué(s)", "p": P["basias"], "poids": 1.0})

    queried = any(rec.has(k) for k in ("basol", "basias_count", "icpe_count", "sis"))
    if not sig:
        if queried:
            sig.append({"key": "basias_count", "label": "aucun signal de pollution dans les bases", "p": P["terrain_aucune"], "poids": 1.0})
        elif pessimiste:
            sig.append({"key": "basias_count", "label": "pollution inconnue, hypothèse défavorable", "p": P["basias"], "poids": 1.0})
        else:
            sig.append({"key": "basias_count", "label": "pollution non renseignée", "p": P["inconnue"], "poids": 1.0})
    return sig


def compute_cost(t: Tracker, usage: str, hyp: dict | None = None) -> dict:
    hyp = hyp or {}
    rec = t.rec
    pess = bool(hyp.get("pessimiste"))
    m_depol = float(hyp.get("cout_depollution_mult", 1.0))
    m_demol = float(hyp.get("cout_demolition_mult", 1.0))

    surf = float(t.read("surface_m2", 0, 2)) or 1.0
    emprise = float(t.read("bati_emprise_m2", 0, 1))
    etat = t.read("bati_etat", "inconnu", 1.5)
    annee = t.read("bati_annee", None, 0.5)
    items: list[dict] = []

    def add(label, lo, hi, key, cat):
        if hi <= 0:
            return
        items.append({"label": label, "bas": lo, "haut": hi, "key": key, "categorie": cat})

    # -------------------------------------------------- démolition / réemploi
    reemploi = usage in ("tertiaire", "activite", "equipement") and etat in ("bon", "moyen")
    frac_demol = 0.3 if reemploi else 1.0
    if etat == "inconnu":
        frac_demol = 0.5 if reemploi else 1.0
    if emprise > 0:
        lo, hi = C.COST["demolition_m2_emprise"]
        if etat in ("degrade", "ruine"):
            lo, hi = lo * 1.2, hi * 1.3
        lbl = ("réemploi partiel du bâti" if reemploi else
               {"degrade": "bâti dégradé à démolir", "ruine": "bâti en ruine à démolir",
                "inconnu": "état du bâti inconnu"}.get(etat, "bâti à démolir"))
        add(lbl.capitalize(), emprise * frac_demol * lo * m_demol, emprise * frac_demol * hi * m_demol, "bati_etat", "demolition")
        # Amiante : obligatoire de repérer avant démolition pour un bâti antérieur au 1er juillet 1997
        a_lo, a_hi = C.COST["desamiantage_m2_emprise"]
        if annee is None:
            lo_f = 0.5 if pess else 0.0
            add("Amiante possible (année de construction inconnue)", emprise * frac_demol * a_lo * lo_f,
                emprise * frac_demol * a_hi, "bati_annee", "demolition")
        elif annee < 1997:
            add(f"Désamiantage probable (bâti de {annee})", emprise * frac_demol * a_lo, emprise * frac_demol * a_hi,
                "bati_annee", "demolition")

    # -------------------------------------------------------------- pollution
    sens = C.USAGE_POLLUTION_SENSITIVITY[usage]
    signals = _pollution_signals(t, pess)
    signals.sort(key=lambda s: -(s["p"][0] * s["p"][2] + s["p"][1] * s["p"][3]))
    pol_labels = []
    for i, s in enumerate(signals):
        clo, chi, flo, fhi = s["p"]
        factor = 1.0 if i == 0 else 0.25  # signaux secondaires : effet de cumul partiel
        lo = surf * clo * flo * sens * factor * m_depol
        hi = surf * chi * fhi * sens * factor * m_depol
        add(f"Dépollution : {s['label']}", lo, hi, s["key"], "pollution")
        if s["p"][1] > 5:
            pol_labels.append(s["label"])

    # ----------------------------------------------------------- viabilisation
    v_lo, v_hi = C.COST["viabilisation"][usage]
    add("Viabilisation / VRD", surf * v_lo, surf * v_hi, None, "viabilisation")
    alea = t.rec.get("alea_argiles")
    if alea in ("fort", "moyen") and usage in ("logement", "tertiaire", "equipement", "activite"):
        a_lo, a_hi = C.COST["fondations_argiles_fort_m2"]
        k = 1.0 if alea == "fort" else 0.5
        add(f"Fondations adaptées (aléa retrait-gonflement des argiles {alea})", surf * a_lo * k, surf * a_hi * k,
            "alea_argiles", "viabilisation")
    if t.rec.get("site_extension"):
        e_lo, e_hi = C.COST["extension_reseaux_m2"]
        add("Extension des réseaux (site en extension urbaine)", surf * e_lo, surf * e_hi, "site_extension", "viabilisation")
    if usage == "photovoltaique":
        d = float(t.read("distance_poste_source_km", 10, 1))
        r_lo, r_hi = C.COST["raccordement_pv_eur_km"]
        add(f"Raccordement au poste source ({d:.0f} km)", d * r_lo, d * r_hi, "distance_poste_source_km", "viabilisation")
    if usage == "renaturation":
        imper = emprise + 0.3 * max(0.0, surf - emprise)
        d_lo, d_hi = C.COST["desimpermeabilisation_m2"]
        add("Désimperméabilisation des sols", imper * d_lo * 0.6, imper * d_hi * 0.6, None, "viabilisation")

    # ----------------------------------------------- géométrie & accessibilité
    base_lo = sum(i["bas"] for i in items)
    base_hi = sum(i["haut"] for i in items)
    geo_pct = 0.0
    geo_lbl = []
    forme = t.read("forme", "compacte", 1)
    if forme == "morcelee":
        geo_pct += 0.15
        geo_lbl.append("site morcelé")
    if t.read("enclave", False, 0.5):
        geo_pct += 0.10
        geo_lbl.append("site enclavé")
    if t.read("acces", "bon", 0.5) == "difficile":
        geo_pct += 0.10
        geo_lbl.append("accès difficile")
    if geo_pct:
        add("Surcoûts de géométrie / accès (" + ", ".join(geo_lbl) + ")", base_lo * geo_pct * 0.5,
            base_hi * geo_pct, "forme", "geometrie")

    # ------------------------------------------------------- statut foncier
    nb_p = t.read("nb_proprietaires", None, 1.5)
    ptype = t.read("proprietaire_type", "inconnu", 1)
    if nb_p is not None and nb_p >= 3:
        f_lo, f_hi = C.COST["foncier_multi_proprio_m2"]
        k = min(2.0, nb_p / 3)
        add(f"Maîtrise foncière : {nb_p} propriétaires distincts", surf * f_lo * k, surf * f_hi * k,
            "nb_proprietaires", "foncier")
    elif nb_p is None and ptype != "public":
        f_lo, f_hi = C.COST["foncier_multi_proprio_m2"]
        add("Maîtrise foncière : nombre de propriétaires inconnu", 0, surf * f_hi * 0.5, "nb_proprietaires", "foncier")

    comp = hyp.get("compensation_zone_humide")
    if comp in ("tous", "concernes") and usage != "renaturation":
        if comp == "tous" or t.rec.get("zone_humide") in ("avere", "probable"):
            c_lo, c_hi = C.COST["compensation_zone_humide_m2"]
            add("Hypothèse : zone humide compensatoire", surf * c_lo, surf * c_hi, "zone_humide", "foncier")

    # --------------------------------------------------- portage (délais)
    extra_months = float(hyp.get("delai_supplementaire_mois", 0))
    if extra_months:
        lo_t = sum(i["bas"] for i in items)
        hi_t = sum(i["haut"] for i in items)
        rate = 0.04 * extra_months / 12  # coût de portage financier ~4 %/an
        add(f"Portage financier (+{extra_months:.0f} mois)", lo_t * rate, hi_t * rate, None, "portage")

    lo = sum(i["bas"] for i in items)
    hi = sum(i["haut"] for i in items)
    q = hyp.get("cost_quantile")
    point = lo + (hi - lo) * (q if q is not None else 0.5)

    lo_m2, hi_m2, pt_m2 = lo / surf, hi / surf, point / surf
    level = min(100.0, pt_m2 / C.COST_SCALE_EUR_M2 * 100)
    uncert = min(100.0, (hi_m2 - lo_m2) / C.UNCERTAINTY_SCALE_EUR_M2 * 100)
    risk = 0.7 * level + 0.3 * uncert
    score = max(0.0, min(100.0, 100 - risk))

    # Contributions pour l'explication : part de chaque poste dans le risque
    tot_mid = sum((i["bas"] + i["haut"]) / 2 for i in items) or 1.0
    # (la viabilisation standard est un coût de référence commun à tous les
    # sites : elle pèse dans le score mais n'est pas présentée comme facteur.)
    for i in sorted(items, key=lambda x: -(x["bas"] + x["haut"])):
        share = ((i["bas"] + i["haut"]) / 2) / tot_mid
        if i["label"] == "Viabilisation / VRD":
            continue
        t.add(i["label"], -risk * share, i["key"])

    if risk >= 60:
        niveau = "très élevé"
    elif risk >= 40:
        niveau = "élevé"
    elif risk >= 20:
        niveau = "modéré"
    else:
        niveau = "faible"

    # Motifs : les postes qui pèsent le plus (hors viabilisation standard)
    motifs = []
    for i in sorted(items, key=lambda x: -(x["bas"] + x["haut"])):
        if i["categorie"] == "viabilisation" and i["label"] == "Viabilisation / VRD":
            continue
        if i["haut"] <= 0 or (i["categorie"] == "pollution" and i["haut"] / surf < 5):
            continue
        lbl = i["label"].replace("Dépollution : ", "").replace("Maîtrise foncière : ", "")
        motifs.append(lbl[0].lower() + lbl[1:])
    if not motifs:
        motifs.append("pas de facteur de surcoût identifié")
    phrase = f"Risque de surcoût {niveau} : " + ", ".join(motifs[:3]) + "."
    _ = pol_labels

    return {
        "score": round(score, 1),
        "risque": niveau,
        "phrase": phrase,
        "total_bas": round(lo),
        "total_haut": round(hi),
        "eur_m2_bas": round(lo_m2, 1),
        "eur_m2_haut": round(hi_m2, 1),
        "postes": [{**i, "bas": round(i["bas"]), "haut": round(i["haut"])} for i in items],
    }
