"""Dimension 4 — Attractivité économique et marché.

Deux composantes à parts égales pour les usages marchands :
  * le marché (prix DVF relatifs, dynamique, liquidité, permis SITADEL, demande),
  * un « compte à rebours » aménageur simplifié : valeur foncière résiduelle
    admissible moins coût de remise en état et coût d'acquisition estimé.
    Le résultat est une FOURCHETTE de bilan net, explicitement grossière.

Pour les usages non marchands (renaturation, équipement), la composante bilan
est remplacée par une valeur d'aménité (population desservie, déficits).
"""
from __future__ import annotations

from .record import Tracker

MARKET_USAGES = ("logement", "tertiaire", "activite", "photovoltaique")


def _clamp(x):
    return max(0.0, min(100.0, x))


def residual_value(t: Tracker, usage: str, shock: float) -> tuple[float, float, str]:
    """Charge foncière admissible (bas, haut) en € et hypothèse résumée."""
    surf = float(t.rec.get("surface_m2", 0))
    dc = t.rec.get("distance_centre_km", 5)
    if usage == "logement":
        dens = 0.9 if dc <= 1 else 0.7 if dc <= 3 else 0.45
        sdp = surf * dens
        prix_ancien = float(t.read("prix_m2_logement", 2500, 1.5))
        prix_neuf = prix_ancien * 1.25 * (1 + shock) * 0.9  # 0.9 : mixité sociale
        rec_ = sdp * prix_neuf
        lo = rec_ * 0.75 - sdp * 2100
        hi = rec_ * 0.80 - sdp * 1700
        return lo, hi, f"SDP ≈ {sdp:,.0f} m² à {prix_neuf:,.0f} €/m², construction 1 700-2 100 €/m²".replace(",", " ")
    if usage == "tertiaire":
        sdp = surf * 0.8
        vt = float(t.read("vacance_tertiaire_pct", 8, 1)) / 100
        prix = float(t.read("prix_m2_logement", 2500, 1)) * 0.85 * (1 - 2 * vt) * (1 + shock)
        rec_ = sdp * prix
        return rec_ * 0.75 - sdp * 2300, rec_ * 0.80 - sdp * 1800, f"SDP ≈ {sdp:,.0f} m² valorisée {prix:,.0f} €/m²".replace(",", " ")
    if usage == "activite":
        p = float(t.read("prix_terrain_activite_m2", 40, 1.5)) * (1 + shock)
        return surf * 0.8 * p * 0.85, surf * 0.85 * p * 1.1, f"terrain cessible à {p:,.0f} €/m²"
    if usage == "photovoltaique":
        ha = surf / 10_000
        irr = float(t.rec.get("irradiation_kwh_m2", 1250))
        loyer = (2500 + (irr - 1100) * 8) * (1 + shock)  # €/ha/an, croît avec le gisement
        return ha * loyer * 10, ha * loyer * 14, f"bail emphytéotique ≈ {loyer:,.0f} €/ha/an capitalisé".replace(",", " ")
    return 0.0, 0.0, "usage non marchand"


def compute_market(t: Tracker, usage: str, cost: dict, hyp: dict | None = None) -> dict:
    hyp = hyp or {}
    shock = float(hyp.get("prix_marche_pct", 0)) / 100
    q = hyp.get("market_noise", 0.0)
    shock += q
    # Les usages non marchands partent plus bas : ils ne dégagent pas de recette.
    s = 50.0 if usage in MARKET_USAGES else 35.0

    if usage in ("logement", "tertiaire"):
        prix = float(t.read("prix_m2_logement", 2500, 1.5)) * (1 + shock)
        # Sans prix de référence départemental, la comparaison est omise plutôt que faite contre un défaut arbitraire
        ref = float(t.read("prix_m2_dept", 0, 0.5)) if t.rec.has("prix_m2_dept") else 0
        r = prix / ref if ref else 1
        if not ref:
            pass
        elif r > 1.2:
            s += t.add(f"Prix DVF {r:.0%} de la référence départementale", 12, "prix_m2_logement")
        elif r > 1.0:
            s += t.add(f"Prix DVF supérieurs à la moyenne départementale ({r:.0%})", 6, "prix_m2_logement")
        elif r < 0.7:
            s += t.add(f"Prix DVF faibles ({r:.0%} de la référence)", -10, "prix_m2_logement")
        ev = float(t.read("evol_prix_3ans_pct", 0, 1))
        if ev > 10:
            s += t.add(f"Marché en hausse (+{ev:.0f} % sur 3 ans)", 10, "evol_prix_3ans_pct")
        elif ev > 0:
            s += t.add(f"Marché stable à haussier (+{ev:.0f} %)", 4, "evol_prix_3ans_pct")
        elif ev < -5:
            s += t.add(f"Marché baissier ({ev:.0f} % sur 3 ans)", -10, "evol_prix_3ans_pct")
        nm = t.read("nb_mutations_3ans", 200, 0.5)
        if nm < 50:
            s += t.add("Marché étroit (< 50 ventes en 3 ans)", -8, "nb_mutations_3ans")
        elif nm > 500:
            s += t.add("Marché liquide (> 500 ventes en 3 ans)", 5, "nb_mutations_3ans")
        if usage == "logement":
            lg = float(t.read("logements_autorises_1000hab", 5, 1))
            if lg > 8:
                s += t.add(f"Dynamisme de construction ({lg:.0f} logts autorisés / 1 000 hab.)", 8, "logements_autorises_1000hab")
            elif lg < 3:
                s += t.add("Peu de permis de construire récents (SITADEL)", -6, "logements_autorises_1000hab")
        else:
            vt = float(t.read("vacance_tertiaire_pct", 8, 1))
            if vt > 12:
                s += t.add(f"Vacance tertiaire élevée ({vt:.0f} %)", -10, "vacance_tertiaire_pct")
        if t.read("distance_gare_km", 5, 0.5) <= 1:
            s += t.add("Gare à proximité (aménité)", 5, "distance_gare_km")
    elif usage == "activite":
        dem = t.read("demande_activite", "moyenne", 1.5)
        s += t.add(f"Demande d'implantation {dem}", {"forte": 15, "moyenne": 3, "faible": -12}[dem], "demande_activite")
        v = float(t.read("vacance_locaux_activite_pct", 8, 1))
        if v > 12:
            s += t.add(f"Vacance de locaux d'activité ({v:.0f} %)", -8, "vacance_locaux_activite_pct")
        elif v < 5:
            s += t.add(f"Marché des locaux tendu ({v:.0f} % de vacance)", 8, "vacance_locaux_activite_pct")
        if t.read("distance_autoroute_km", 10, 1) <= 5:
            s += t.add("Accessibilité poids lourds", 6, "distance_autoroute_km")
    elif usage == "photovoltaique":
        irr = float(t.read("irradiation_kwh_m2", 1250, 1.5))
        s += t.add(f"Productible solaire ({irr:.0f} kWh/m²/an)", (irr - 1300) / 15, "irradiation_kwh_m2")
        if t.read("distance_poste_source_km", 10, 1) > 15:
            s += t.add("Coût de raccordement pénalisant", -8, "distance_poste_source_km")
    elif usage == "renaturation":
        pop = float(t.read("population", 20_000, 1))
        if pop > 50_000:
            s += t.add("Forte population bénéficiaire", 10, "population")
        if t.read("espaces_verts_m2_hab", 15, 1) < 10:
            s += t.add("Valeur d'aménité élevée (déficit d'espaces verts)", 12, "espaces_verts_m2_hab")
        if t.read("ilot_chaleur", False, 0.5):
            s += t.add("Rafraîchissement urbain attendu", 6, "ilot_chaleur")
    elif usage == "equipement":
        pop = float(t.read("population", 20_000, 1))
        if pop > 50_000:
            s += t.add("Bassin de population important", 10, "population")
        de = t.read("deficit_equipements", "moyen", 1)
        s += t.add(f"Besoin d'équipement {de}", {"fort": 12, "moyen": 3, "faible": -8}[de], "deficit_equipements")
    marche = _clamp(s)

    # ------------------------------------------------------------ bilan net
    surf = float(t.rec.get("surface_m2", 0)) or 1.0
    vlo, vhi, hyp_txt = residual_value(t, usage, shock)
    p_fr = float(t.read("prix_terrain_friche_m2", 20, 0.5))
    acq_k = 0.5 if t.rec.get("proprietaire_type") == "public" else 1.0
    alo, ahi = surf * p_fr * 0.6 * acq_k, surf * p_fr * 1.4 * acq_k
    net_lo = vlo - cost["total_haut"] - ahi
    net_hi = vhi - cost["total_bas"] - alo
    # Contre-analyse : subvention d'un montant donné, pour tous les sites ou seulement les éligibles au Fonds Vert
    sub = float(hyp.get("subvention_eur") or 0)
    if sub and not hyp.get("sans_subventions"):
        cible = hyp.get("subvention_cible", "eligibles_fonds_vert")
        eligible = (usage in ("logement", "activite", "tertiaire", "equipement") and net_lo < 0
                    and not t.rec.get("site_extension"))
        if cible == "tous" or eligible:
            net_lo += sub
            net_hi += sub
            t.add(f"Hypothèse : subvention de {sub / 1000:,.0f} k€".replace(",", " "), min(25.0, sub / surf / 8), None)
    net_mid_m2 = (net_lo + net_hi) / 2 / surf

    if usage in MARKET_USAGES:
        bilan_score = _clamp(50 + net_mid_m2 / 4)
        score = 0.5 * marche + 0.5 * bilan_score
        if net_hi < 0:
            t.add("Bilan aménageur négatif même en hypothèse haute", -(50 - bilan_score) * 0.5, "prix_m2_logement")
            t.alert("info", "Bilan d'opération structurellement déficitaire : une subvention (ex. Fonds Vert) serait nécessaire.")
        elif net_lo > 0:
            t.add("Bilan aménageur positif même en hypothèse basse", (bilan_score - 50) * 0.5, "prix_m2_logement")
        else:
            t.add("Bilan aménageur incertain (fourchette à cheval sur zéro)", (bilan_score - 50) * 0.5, "prix_m2_logement")
    else:
        score = marche
    lvl = "forte" if score >= 65 else "moyenne" if score >= 45 else "faible"
    phrase = f"Attractivité {lvl}"
    if usage in MARKET_USAGES:
        phrase += f" : bilan net estimé entre {net_lo/1e6:+.1f} et {net_hi/1e6:+.1f} M€ ({hyp_txt})."
    else:
        phrase += " : usage non marchand, évalué sur sa valeur d'aménité."
    return {
        "score": round(score, 1),
        "marche": round(marche, 1),
        "phrase": phrase,
        "valeur_residuelle_bas": round(vlo),
        "valeur_residuelle_haut": round(vhi),
        "acquisition_bas": round(alo),
        "acquisition_haut": round(ahi),
        "bilan_net_bas": round(net_lo),
        "bilan_net_haut": round(net_hi),
        "hypothese": hyp_txt,
        "marchand": usage in MARKET_USAGES,
    }
