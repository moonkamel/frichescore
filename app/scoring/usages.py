"""Dimension 1 — Potentiel d'usage.

Une fonction de scoring PAR USAGE : les critères de pertinence d'une friche pour
du logement n'ont rien à voir avec ceux du photovoltaïque. Chaque fonction part
d'une base de 50 et ajoute des contributions explicites (+/-), conservées pour
l'explication du score.

Retour : (score 0-100, liste de motifs rédhibitoires).
"""
from __future__ import annotations

from .record import Tracker

BASE = 40.0


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def _ha(t: Tracker) -> float:
    return t.read("surface_m2", 0, importance=2) / 10_000


def _common_blockers(t: Tracker, usage: str) -> list[str]:
    b = []
    captage = t.read("captage", "non", 0.5)
    if captage == "immediat":
        b.append("Périmètre de protection immédiate de captage : toute activité y est interdite")
    if usage in ("logement", "equipement"):
        if t.read("ppri", "non", 1) == "rouge":
            b.append("Zone rouge de PPRi : constructions nouvelles à usage sensible interdites")
        if t.read("pprt", False, 0.5):
            b.append("Périmètre de PPRT : usages sensibles très contraints")
    return b


def score_logement(t: Tracker) -> tuple[float, list[str]]:
    s = BASE
    ha = _ha(t)
    if ha < 0.2:
        s += t.add("Surface < 2 000 m² : opération de logement marginale", -10, "surface_m2")
    elif ha <= 5:
        s += t.add(f"Surface adaptée à une opération de logement ({ha:.1f} ha)", 10, "surface_m2")
    elif ha > 15:
        s += t.add(f"Très grande emprise ({ha:.0f} ha) : opération longue à phaser", -5, "surface_m2")

    dc = t.read("distance_centre_km", 5, 1)
    if dc <= 1:
        s += t.add("À moins d'1 km du centre-ville", 10, "distance_centre_km")
    elif dc <= 3:
        s += t.add("Proche du centre (< 3 km)", 5, "distance_centre_km")
    elif dc > 8:
        s += t.add(f"Éloigné du centre ({dc:.0f} km)", -10, "distance_centre_km")

    tc = t.read("desserte_tc", "moyenne", 1)
    s += t.add({"forte": "Desserte TC forte", "moyenne": "Desserte TC moyenne", "faible": "Desserte TC faible"}[tc],
               {"forte": 10, "moyenne": 4, "faible": -8}[tc], "desserte_tc")
    if t.read("distance_gare_km", 5, 0.5) <= 1:
        s += t.add("Gare à moins d'1 km", 6, "distance_gare_km")

    z = t.read("zonage_abc", "B2", 1.5)
    s += t.add(f"Zone {z} (tension du marché du logement)", {"Abis": 15, "A": 15, "B1": 8, "B2": 0, "C": -12}.get(z, 0), "zonage_abc")
    vac = t.read("vacance_logement_pct", 8, 1)
    if vac > 10:
        s += t.add(f"Vacance de logements élevée ({vac:.0f} %)", -10, "vacance_logement_pct")
    elif vac < 6:
        s += t.add(f"Vacance faible ({vac:.0f} %) : demande non satisfaite", 6, "vacance_logement_pct")

    voc = t.read("plu_vocation", None, 1)
    zone = t.read("plu_zone", "inconnu", 1)
    if voc in ("habitat", "mixte"):
        s += t.add("Vocation PLU compatible (habitat/mixte)", 6, "plu_vocation")
    elif voc == "activite":
        s += t.add("Vocation PLU économique : changement de zonage requis", -6, "plu_vocation")
    if zone in ("A", "N"):
        s += t.add(f"Zone {zone} au PLU", -12, "plu_zone")

    tf = t.read("type_friche", "inconnu", 0.5)
    if tf in ("habitat", "commerciale", "hospitaliere", "militaire", "tertiaire"):
        s += t.add(f"Friche {tf} : reconversion résidentielle courante", 4, "type_friche")
    elif tf in ("carriere",):
        s += t.add("Ancienne carrière : site peu adapté au logement", -10, "type_friche")
    if t.read("seveso", None, 0.5):
        s += t.add("Établissement SEVESO : usage résidentiel très délicat", -15, "seveso")
    return _clamp(s), _common_blockers(t, "logement")


def score_activite(t: Tracker) -> tuple[float, list[str]]:
    s = BASE
    ha = _ha(t)
    if ha >= 5:
        s += t.add(f"Grande emprise ({ha:.0f} ha) recherchée pour l'industrie/logistique", 18, "surface_m2")
    elif ha >= 2:
        s += t.add(f"Emprise de {ha:.1f} ha adaptée à de l'activité", 12, "surface_m2")
    elif ha < 0.5:
        s += t.add("Emprise < 5 000 m² : trop petite pour la plupart des projets", -10, "surface_m2")

    da = t.read("distance_autoroute_km", 10, 1.5)
    if da <= 3:
        s += t.add("Échangeur autoroutier à moins de 3 km", 15, "distance_autoroute_km")
    elif da <= 10:
        s += t.add("Accès autoroutier < 10 km", 6, "distance_autoroute_km")
    elif da > 20:
        s += t.add(f"Accès autoroutier éloigné ({da:.0f} km)", -12, "distance_autoroute_km")

    dem = t.read("demande_activite", "moyenne", 1.5)
    s += t.add(f"Demande foncière économique {dem}", {"forte": 12, "moyenne": 4, "faible": -8}[dem], "demande_activite")
    if t.read("vacance_locaux_activite_pct", 8, 0.5) > 12:
        s += t.add("Vacance élevée des locaux d'activité", -8, "vacance_locaux_activite_pct")

    tf = t.read("type_friche", "inconnu", 0.5)
    if tf in ("industrielle", "ferroviaire", "portuaire", "logistique"):
        s += t.add(f"Vocation économique historique (friche {tf})", 8, "type_friche")
    voc = t.read("plu_vocation", None, 1)
    zone = t.read("plu_zone", "inconnu", 1)
    if voc == "activite":
        s += t.add("Zonage PLU économique", 8, "plu_vocation")
    elif voc == "habitat":
        s += t.add("Zonage PLU résidentiel : changement requis", -6, "plu_vocation")
    if zone in ("A", "N"):
        s += t.add(f"Zone {zone} au PLU", -10, "plu_zone")
    if t.read("territoire_industrie", False, 0.5):
        s += t.add("Commune labellisée Territoire d'industrie", 5, "territoire_industrie")
    if t.read("distance_centre_km", 5, 0.5) < 1:
        s += t.add("En cœur urbain : conflits de nuisances (trafic, bruit)", -5, "distance_centre_km")
    forme = t.read("forme", "compacte", 0.5)
    if forme == "compacte":
        s += t.add("Géométrie compacte", 4, "forme")
    elif forme == "morcelee":
        s += t.add("Site morcelé : implantation de grands bâtiments difficile", -8, "forme")
    return _clamp(s), _common_blockers(t, "activite")


def score_tertiaire(t: Tracker) -> tuple[float, list[str]]:
    s = BASE
    dg = t.read("distance_gare_km", 5, 1.5)
    if dg <= 0.5:
        s += t.add("Quartier de gare (< 500 m)", 15, "distance_gare_km")
    elif dg <= 1.5:
        s += t.add("Gare à moins de 1,5 km", 8, "distance_gare_km")
    elif dg > 5:
        s += t.add(f"Loin d'une gare ({dg:.0f} km)", -12, "distance_gare_km")
    pop = t.read("population", 20_000, 1)
    if pop > 100_000:
        s += t.add("Pôle urbain > 100 000 hab.", 10, "population")
    elif pop > 30_000:
        s += t.add("Ville moyenne > 30 000 hab.", 4, "population")
    elif pop < 10_000:
        s += t.add("Commune < 10 000 hab. : marché tertiaire étroit", -10, "population")
    vt = t.read("vacance_tertiaire_pct", 8, 1.5)
    if vt > 12:
        s += t.add(f"Vacance de bureaux élevée ({vt:.0f} %)", -12, "vacance_tertiaire_pct")
    elif vt < 6:
        s += t.add(f"Vacance de bureaux faible ({vt:.0f} %)", 6, "vacance_tertiaire_pct")
    ha = _ha(t)
    if 0.2 <= ha <= 3:
        s += t.add("Emprise adaptée à un programme tertiaire", 6, "surface_m2")
    elif ha > 10:
        s += t.add("Emprise surdimensionnée pour du tertiaire", -8, "surface_m2")
    etat = t.read("bati_etat", "inconnu", 1)
    if etat in ("bon", "moyen") and t.read("bati_emprise_m2", 0, 0.5) > 500:
        s += t.add(f"Bâti en état {etat} : réhabilitation envisageable", 8, "bati_etat")
    if t.read("plu_zone", "inconnu", 0.5) == "U":
        s += t.add("Zone urbaine au PLU", 4, "plu_zone")
    return _clamp(s), _common_blockers(t, "tertiaire")


def score_photovoltaique(t: Tracker) -> tuple[float, list[str]]:
    s = BASE
    irr = t.read("irradiation_kwh_m2", 1250, 1.5)
    if irr >= 1500:
        s += t.add(f"Gisement solaire excellent ({irr:.0f} kWh/m²/an)", 20, "irradiation_kwh_m2")
    elif irr >= 1350:
        s += t.add(f"Bon gisement solaire ({irr:.0f} kWh/m²/an)", 10, "irradiation_kwh_m2")
    elif irr < 1150:
        s += t.add(f"Gisement solaire faible ({irr:.0f} kWh/m²/an)", -10, "irradiation_kwh_m2")
    ha = _ha(t)
    if ha >= 5:
        s += t.add(f"Emprise de {ha:.0f} ha : centrale de taille significative", 18, "surface_m2")
    elif ha >= 1:
        s += t.add(f"Emprise de {ha:.1f} ha suffisante pour une centrale au sol", 12, "surface_m2")
    elif ha < 0.5:
        s += t.add("Emprise < 5 000 m² : centrale au sol peu rentable", -20, "surface_m2")
    dps = t.read("distance_poste_source_km", 10, 1.5)
    if dps <= 5:
        s += t.add(f"Poste source à {dps:.0f} km", 12, "distance_poste_source_km")
    elif dps <= 15:
        s += t.add(f"Poste source à {dps:.0f} km", 4, "distance_poste_source_km")
    elif dps > 20:
        s += t.add(f"Poste source éloigné ({dps:.0f} km) : raccordement coûteux", -10, "distance_poste_source_km")
    tf = t.read("type_friche", "inconnu", 0.5)
    pollue = bool(t.rec.get("basol") or t.rec.get("basias_count", 0) > 0 or t.rec.get("sis"))
    if tf in ("carriere", "decharge", "industrielle", "militaire", "ferroviaire") or pollue:
        s += t.add("Site dégradé : éligible aux appels d'offres CRE « terrains dégradés »", 8, "type_friche")
    emprise = t.read("bati_emprise_m2", 0, 0.5)
    surf = max(1.0, t.rec.get("surface_m2", 1))
    if emprise / surf > 0.4:
        s += t.add("Emprise bâtie > 40 % : démolition préalable importante", -10, "bati_emprise_m2")
    if t.read("abf", False, 0.5):
        s += t.add("Périmètre de monument historique (avis ABF)", -10, "abf")
    if t.read("natura2000", False, 0.5):
        s += t.add("Site Natura 2000", -8, "natura2000")
    if t.read("zone_humide", "non", 0.5) == "avere":
        s += t.add("Zone humide avérée", -15, "zone_humide")
    if t.read("plu_zone", "inconnu", 0.5) == "A":
        s += t.add("Zone agricole au PLU", -8, "plu_zone")
    if t.read("distance_centre_km", 5, 0.3) < 1:
        s += t.add("En cœur urbain : foncier à plus forte valeur ailleurs", -8, "distance_centre_km")
    return _clamp(s), _common_blockers(t, "photovoltaique")


def score_renaturation(t: Tracker) -> tuple[float, list[str]]:
    s = BASE
    zh = t.read("zone_humide", "non", 1)
    if zh in ("avere", "probable"):
        s += t.add(f"Zone humide {'avérée' if zh == 'avere' else 'probable'} : potentiel de restauration", 15, "zone_humide")
    ppri = t.read("ppri", "non", 1)
    if ppri in ("rouge", "bleu"):
        s += t.add(f"Zone inondable ({ppri}) : intérêt comme champ d'expansion des crues", 12, "ppri")
    if t.read("natura2000", False, 0.5) or t.read("znieff", False, 0.5):
        s += t.add("Continuité écologique (Natura 2000 / ZNIEFF)", 10, "znieff")
    ev = t.read("espaces_verts_m2_hab", 15, 1)
    if ev < 10:
        s += t.add(f"Déficit d'espaces verts ({ev:.0f} m²/hab.)", 12, "espaces_verts_m2_hab")
    if t.read("ilot_chaleur", False, 0.5):
        s += t.add("Îlot de chaleur urbain identifié", 10, "ilot_chaleur")
    if t.rec.get("pollution_constatee") == "forte":
        s += t.add("Pollution forte : renaturation sous phytogestion uniquement", -10, "pollution_constatee")
    prix = t.read("prix_m2_logement", 2500, 0.5)
    if prix < 1800:
        s += t.add("Faible valeur foncière : coût d'opportunité réduit", 6, "prix_m2_logement")
    elif prix > 4500:
        s += t.add("Forte valeur foncière : coût d'opportunité élevé", -8, "prix_m2_logement")
    if t.read("plu_zone", "inconnu", 0.5) in ("N", "A"):
        s += t.add("Zonage naturel ou agricole au PLU", 8, "plu_zone")
    if _ha(t) >= 0.5:
        s += t.add("Surface suffisante pour un projet écologique", 5, "surface_m2")
    return _clamp(s), _common_blockers(t, "renaturation")


def score_equipement(t: Tracker) -> tuple[float, list[str]]:
    s = BASE
    de = t.read("deficit_equipements", "moyen", 1.5)
    s += t.add(f"Déficit d'équipements {de}", {"fort": 15, "moyen": 5, "faible": -8}[de], "deficit_equipements")
    if t.read("distance_centre_km", 5, 1) <= 2:
        s += t.add("Proche du centre (< 2 km)", 10, "distance_centre_km")
    if t.read("desserte_tc", "moyenne", 1) == "forte":
        s += t.add("Desserte TC forte", 8, "desserte_tc")
    pt = t.read("proprietaire_type", "inconnu", 1)
    if pt == "public":
        s += t.add("Propriété publique : maîtrise foncière facilitée", 10, "proprietaire_type")
    if t.read("population", 20_000, 0.5) > 20_000:
        s += t.add("Bassin de population > 20 000 hab.", 5, "population")
    ha = _ha(t)
    if 0.3 <= ha <= 5:
        s += t.add("Emprise adaptée à un équipement", 6, "surface_m2")
    elif ha > 10:
        s += t.add("Emprise très supérieure aux besoins d'un équipement", -6, "surface_m2")
    return _clamp(s), _common_blockers(t, "equipement")


SCORERS = {
    "logement": score_logement,
    "activite": score_activite,
    "tertiaire": score_tertiaire,
    "photovoltaique": score_photovoltaique,
    "renaturation": score_renaturation,
    "equipement": score_equipement,
}
