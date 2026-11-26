"""Jeu de données de DÉMONSTRATION.

⚠️ Toutes les friches générées ici sont SYNTHÉTIQUES. Les communes et EPCI sont
réels, leurs ordres de grandeur (population, prix) sont plausibles, mais les
valeurs ne proviennent pas des bases officielles. Chaque provenance est suffixée
« (démo) » pour que l'interface ne puisse jamais les présenter comme réelles.

Le générateur est déterministe (graine fixe) pour des tests reproductibles.
"""
from __future__ import annotations

import random

from ..scoring.record import Provenance, Record

DEMO = " (démo)"

# code, nom, dept, epci, lat, lon, pop, zonage, prix_m2, irr, profil
# profil : "ind" (industriel), "urb" (métropole), "port", "mixte"
COMMUNES = [
    ("59350", "Lille", "59", "Métropole Européenne de Lille", 50.633, 3.058, 236000, "A", 3700, 1150, "urb"),
    ("59512", "Roubaix", "59", "Métropole Européenne de Lille", 50.691, 3.174, 98000, "B1", 1750, 1150, "ind"),
    ("59599", "Tourcoing", "59", "Métropole Européenne de Lille", 50.723, 3.161, 99000, "B1", 1900, 1150, "ind"),
    ("59183", "Dunkerque", "59", "CU de Dunkerque", 51.034, 2.377, 87000, "B2", 1900, 1140, "port"),
    ("59606", "Valenciennes", "59", "Valenciennes Métropole", 50.357, 3.523, 43000, "B2", 1700, 1160, "mixte"),
    ("59172", "Denain", "59", "CA de la Porte du Hainaut", 50.329, 3.395, 20000, "C", 1000, 1160, "ind"),
    ("59392", "Maubeuge", "59", "CA Maubeuge Val de Sambre", 50.278, 3.973, 29000, "C", 1100, 1170, "ind"),
    ("59178", "Douai", "59", "Douaisis Agglo", 50.371, 3.080, 39000, "B2", 1500, 1160, "mixte"),
    ("57463", "Metz", "57", "Metz Métropole", 49.120, 6.176, 120000, "B1", 2300, 1200, "urb"),
    ("57672", "Thionville", "57", "CA Portes de France-Thionville", 49.358, 6.168, 41000, "B1", 2600, 1190, "mixte"),
    ("57306", "Hayange", "57", "CA du Val de Fensch", 49.329, 6.062, 16000, "B2", 1500, 1190, "ind"),
    ("57221", "Florange", "57", "CA du Val de Fensch", 49.323, 6.121, 11500, "B2", 1700, 1190, "ind"),
    ("57227", "Forbach", "57", "CA Forbach Porte de France", 49.188, 6.896, 21000, "C", 1000, 1210, "ind"),
    ("57606", "Saint-Avold", "57", "CA Saint-Avold Synergie", 49.104, 6.707, 15500, "C", 1100, 1210, "ind"),
    ("76540", "Rouen", "76", "Métropole Rouen Normandie", 49.443, 1.099, 114000, "B1", 2600, 1180, "urb"),
    ("76498", "Le Petit-Quevilly", "76", "Métropole Rouen Normandie", 49.424, 1.060, 22000, "B1", 2000, 1180, "ind"),
    ("76231", "Elbeuf", "76", "Métropole Rouen Normandie", 49.287, 1.008, 16000, "B2", 1500, 1180, "ind"),
    ("76351", "Le Havre", "76", "Le Havre Seine Métropole", 49.494, 0.108, 166000, "B1", 2100, 1200, "port"),
    ("76305", "Gonfreville-l'Orcher", "76", "Le Havre Seine Métropole", 49.505, 0.234, 9000, "B2", 1600, 1200, "port"),
    ("76217", "Dieppe", "76", "CA de la Région Dieppoise", 49.922, 1.078, 28000, "B2", 2000, 1170, "mixte"),
    ("69123", "Lyon", "69", "Métropole de Lyon", 45.764, 4.836, 520000, "A", 5000, 1400, "urb"),
    ("69266", "Villeurbanne", "69", "Métropole de Lyon", 45.771, 4.880, 155000, "A", 4200, 1400, "urb"),
    ("69259", "Vénissieux", "69", "Métropole de Lyon", 45.698, 4.886, 67000, "B1", 2800, 1400, "ind"),
    ("69199", "Saint-Fons", "69", "Métropole de Lyon", 45.704, 4.853, 19000, "B1", 2500, 1400, "ind"),
    ("69256", "Vaulx-en-Velin", "69", "Métropole de Lyon", 45.778, 4.921, 52000, "A", 3000, 1400, "mixte"),
    ("69091", "Givors", "69", "Métropole de Lyon", 45.590, 4.769, 20000, "B1", 2300, 1420, "ind"),
    ("13055", "Marseille", "13", "Métropole Aix-Marseille-Provence", 43.296, 5.370, 870000, "A", 3600, 1700, "urb"),
    ("13039", "Fos-sur-Mer", "13", "Métropole Aix-Marseille-Provence", 43.438, 4.945, 16000, "B1", 2800, 1720, "port"),
    ("13056", "Martigues", "13", "Métropole Aix-Marseille-Provence", 43.405, 5.048, 49000, "B1", 3200, 1720, "port"),
    ("13041", "Gardanne", "13", "Métropole Aix-Marseille-Provence", 43.455, 5.469, 21000, "B1", 3400, 1700, "ind"),
    ("13078", "Port-Saint-Louis-du-Rhône", "13", "Métropole Aix-Marseille-Provence", 43.387, 4.803, 8500, "B2", 2100, 1720, "port"),
    ("13004", "Arles", "13", "CA Arles-Crau-Camargue-Montagnette", 43.677, 4.630, 51000, "B2", 2500, 1710, "mixte"),
]

DEPTS = {"59": ("Nord", "Hauts-de-France", 2200), "57": ("Moselle", "Grand Est", 1900),
         "76": ("Seine-Maritime", "Normandie", 2200), "69": ("Rhône", "Auvergne-Rhône-Alpes", 3900),
         "13": ("Bouches-du-Rhône", "Provence-Alpes-Côte d'Azur", 3500)}

LITTORAL = {"59183", "76351", "76305", "76217", "13039", "13056", "13078", "13055"}
ACV = {"59512", "59599", "59606", "59392", "59178", "57672", "57227", "76351", "76217", "69259", "13004"}
PVD = {"59172", "57306", "57221", "57606", "76231", "69091", "13078"}
TI = {"59183", "59172", "59392", "57306", "57221", "57606", "76305", "76351", "69199", "13039", "13056", "13041"}

TYPES_BY_PROFILE = {
    "ind": [("industrielle", 50), ("commerciale", 10), ("ferroviaire", 8), ("habitat", 8), ("tertiaire", 6),
            ("logistique", 6), ("hospitaliere", 3), ("militaire", 3), ("carriere", 3), ("decharge", 3)],
    "urb": [("industrielle", 22), ("commerciale", 18), ("tertiaire", 16), ("ferroviaire", 10), ("hospitaliere", 8),
            ("habitat", 12), ("militaire", 6), ("logistique", 8)],
    "port": [("portuaire", 30), ("industrielle", 35), ("logistique", 12), ("commerciale", 8), ("ferroviaire", 7),
             ("decharge", 4), ("militaire", 4)],
    "mixte": [("industrielle", 32), ("commerciale", 18), ("ferroviaire", 10), ("habitat", 12), ("tertiaire", 8),
              ("hospitaliere", 6), ("militaire", 6), ("carriere", 4), ("logistique", 4)],
}

LIEUX = ["des Forges", "du Canal", "de la Gare", "Saint-Éloi", "des Tanneurs", "du Port", "des Aciéries", "de la Plaine",
         "du Moulin", "des Filatures", "de la Voie Ferrée", "des Docks", "du Pont", "des Marais", "Pasteur", "Jean-Jaurès",
         "des Peupliers", "de la Briqueterie", "du Haut-Fourneau", "des Ateliers", "du Lavoir", "de l'Écluse", "Sainte-Barbe",
         "du Bassin", "de la Cokerie", "des Verriers", "du Viaduc", "du Faubourg", "des Rails", "de la Sucrerie"]

TYPE_LABEL = {"industrielle": "Ancien site industriel", "commerciale": "Ancien centre commercial", "ferroviaire": "Emprise ferroviaire",
              "portuaire": "Terre-plein portuaire", "militaire": "Ancienne caserne", "hospitaliere": "Ancien hôpital",
              "habitat": "Îlot d'habitat dégradé", "tertiaire": "Ancien immeuble de bureaux", "carriere": "Ancienne carrière",
              "decharge": "Ancienne décharge", "logistique": "Ancienne plateforme logistique"}

SURFACE_HA = {"industrielle": (0.5, 25), "commerciale": (0.3, 4), "ferroviaire": (0.8, 12), "portuaire": (2, 40),
              "militaire": (2, 30), "hospitaliere": (0.8, 5), "habitat": (0.08, 1.2), "tertiaire": (0.1, 1.5),
              "carriere": (3, 30), "decharge": (2, 20), "logistique": (1.5, 15)}


def _wchoice(rng, items):
    tot = sum(w for _, w in items)
    x = rng.uniform(0, tot)
    for v, w in items:
        x -= w
        if x <= 0:
            return v
    return items[-1][0]


def _logu(rng, lo, hi):
    import math
    return math.exp(rng.uniform(math.log(lo), math.log(hi)))


def commune_records(rng: random.Random) -> dict[str, Record]:
    out = {}
    for code, nom, dep, epci, lat, lon, pop, zabc, prix, irr, prof in COMMUNES:
        r = Record()
        dvf = Provenance("DVF — médiane appartements+maisons" + DEMO, "2025-06", "eleve")
        insee = Provenance("INSEE" + DEMO, "2022", "eleve")
        r.set("population", pop, insee)
        r.set("zonage_abc", zabc, Provenance("Zonage ABC" + DEMO, "2024", "eleve"))
        r.set("prix_m2_logement", prix, dvf)
        r.set("prix_m2_dept", DEPTS[dep][2], dvf)
        trend = {"urb": (2, 12), "ind": (-6, 6), "port": (-2, 8), "mixte": (-4, 8)}[prof]
        r.set("evol_prix_3ans_pct", round(rng.uniform(*trend), 1), dvf)
        r.set("nb_mutations_3ans", int(pop * rng.uniform(0.008, 0.02)), dvf)
        r.set("vacance_logement_pct", round({"urb": rng.uniform(5, 8), "ind": rng.uniform(8, 13),
                                               "port": rng.uniform(7, 10), "mixte": rng.uniform(7, 11)}[prof], 1),
              Provenance("INSEE / LOVAC" + DEMO, "2023", "eleve"))
        r.set("logements_autorises_1000hab", round({"urb": rng.uniform(5, 11), "ind": rng.uniform(1.5, 5),
                                                     "port": rng.uniform(3, 7), "mixte": rng.uniform(2.5, 7)}[prof], 1),
              Provenance("SITADEL" + DEMO, "2025-03", "eleve"))
        r.set("vacance_tertiaire_pct", round(rng.uniform(4, 15), 1), Provenance("Observatoire immobilier local" + DEMO, "2024", "faible"))
        r.set("vacance_locaux_activite_pct", round(rng.uniform(3, 14), 1), Provenance("Fichiers fonciers" + DEMO, "2023", "faible"))
        r.set("demande_activite", _wchoice(rng, [("forte", 3 if prof in ("port", "ind") else 2), ("moyenne", 3), ("faible", 2)]),
              Provenance("Agence de développement" + DEMO, "2024", "faible"))
        r.set("prix_terrain_activite_m2", round(rng.uniform(25, 60) * (1.6 if prof == "urb" else 1.0)),
              Provenance("DVF — terrains d'activité" + DEMO, "2025-06", "moyen"))
        r.set("prix_terrain_friche_m2", round(rng.uniform(10, 35) * (1.8 if prof == "urb" else 1.0)),
              Provenance("Références EPF" + DEMO, "2023", "faible"))
        r.set("irradiation_kwh_m2", irr, Provenance("PVGIS" + DEMO, "2023", "eleve"))
        r.set("deficit_equipements", _wchoice(rng, [("fort", 2), ("moyen", 3), ("faible", 2)]), Provenance("BPE INSEE" + DEMO, "2023", "moyen"))
        r.set("espaces_verts_m2_hab", round(rng.uniform(5, 25), 1), Provenance("OCS GE" + DEMO, "2021", "moyen"))
        r.set("conso_enaf_ha_2011_2021", round(rng.uniform(8, 120) * (2 if pop > 100000 else 1), 1),
              Provenance("Portail de l'artificialisation" + DEMO, "2023", "eleve"))
        if code in ACV:
            r.set("programme_territorial", "ACV", Provenance("ANCT" + DEMO, "2024", "eleve"))
        elif code in PVD:
            r.set("programme_territorial", "PVD", Provenance("ANCT" + DEMO, "2024", "eleve"))
        r.set("territoire_industrie", code in TI, Provenance("ANCT" + DEMO, "2024", "eleve"))
        r.set("loi_littoral", code in LITTORAL, Provenance("DGALN" + DEMO, "2024", "eleve"))
        r.set("fonds_regional", f"Dispositif régional friches — {DEPTS[dep][1]}" + DEMO, Provenance("Région" + DEMO, "2025", "moyen"))
        r.values.update({"commune": nom, "code_insee": code, "departement": dep, "epci": epci,
                         "_lat": lat, "_lon": lon, "_profil": prof, "region": DEPTS[dep][1]})
        out[code] = r
    return out


def friche_records(rng: random.Random, communes: dict[str, Record]) -> list[Record]:
    out = []
    n_id = 0
    for code, c in communes.items():
        pop = c.get("population")
        prof = c.get("_profil")
        n = max(3, min(14, int(3 + (pop / 60000) ** 0.6 * 4 + (3 if prof in ("ind", "port") else 0))))
        n = int(n * rng.uniform(0.8, 1.2))
        for _ in range(n):
            n_id += 1
            out.append(_one_friche(rng, c, n_id))
    return out


def _one_friche(rng: random.Random, c: Record, n_id: int) -> Record:
    r = Record()
    code = c.get("code_insee")
    tf = _wchoice(rng, TYPES_BY_PROFILE[c.get("_profil")])
    verifiee = rng.random() < 0.3
    cf = Provenance("Cartofriches" + DEMO, f"2025-{rng.randint(1, 9):02d}", "moyen" if verifiee else "faible",
                    "fiche vérifiée par l'observatoire local" if verifiee else "repérage non vérifié")
    cf_hi = Provenance("Cartofriches" + DEMO, cf.date, "eleve" if verifiee else "moyen")
    fid = f"DEMO-{code}-{n_id:04d}"
    r.set("id", fid)
    r.set("nom", f"{TYPE_LABEL[tf]} {rng.choice(LIEUX)}", cf_hi)
    r.set("commune", c.get("commune"))
    r.set("code_insee", code)
    r.set("departement", c.get("departement"))
    r.set("epci", c.get("epci"))
    r.set("region", c.get("region"))
    r.set("lat", round(c.get("_lat") + rng.uniform(-0.035, 0.035), 5), cf_hi)
    r.set("lon", round(c.get("_lon") + rng.uniform(-0.05, 0.05), 5), cf_hi)
    r.set("type_friche", tf, cf_hi)
    r.set("statut_cartofriches", "verifiee" if verifiee else "non_verifiee", cf_hi)
    ha = _logu(rng, *SURFACE_HA[tf])
    r.set("surface_m2", round(ha * 10_000), cf)

    # Localisation fine
    urb = c.get("_profil") == "urb"
    dc = round(_logu(rng, 0.3, 4 if urb else 7), 1)
    r.set("distance_centre_km", dc, Provenance("Calcul géographique" + DEMO, "2025", "eleve"))
    r.set("distance_gare_km", round(max(0.2, dc * rng.uniform(0.4, 1.4)), 1), Provenance("SNCF open data" + DEMO, "2025", "eleve"))
    r.set("desserte_tc", "forte" if (dc < 1.5 and urb) or dc < 0.8 else "moyenne" if dc < 4 else "faible",
          Provenance("GTFS" + DEMO, "2025", "moyen"))
    r.set("distance_autoroute_km", round(_logu(rng, 0.5, 18), 1), Provenance("BD TOPO" + DEMO, "2024", "eleve"))
    r.set("distance_poste_source_km", round(_logu(rng, 1, 25), 1), Provenance("Caparéseau" + DEMO, "2024", "moyen"))

    # Bâti
    if tf in ("carriere", "decharge"):
        emprise = 0
    else:
        emprise = round(ha * 10_000 * rng.uniform(0.05, 0.6 if tf != "portuaire" else 0.25))
    r.set("bati_emprise_m2", emprise, Provenance("BD TOPO" + DEMO, "2024", "moyen"))
    if emprise:
        etat = _wchoice(rng, [("bon", 1), ("moyen", 3), ("degrade", 4), ("ruine", 1), ("inconnu", 2)])
        r.set("bati_etat", etat, Provenance("Cartofriches (déclaratif)" + DEMO if verifiee else "Photo-interprétation" + DEMO,
                                            cf.date, "moyen" if verifiee else "faible"))
        if rng.random() < 0.7:
            r.set("bati_annee", rng.randint(1880, 2008), Provenance("Fichiers fonciers" + DEMO, "2024", "moyen"))

    # Foncier & géométrie
    ff = Provenance("Fichiers fonciers" + DEMO, "2024", "eleve")
    if rng.random() < 0.85:
        ptype = _wchoice(rng, [("public", 25), ("prive", 60), ("mixte", 15)])
        r.set("proprietaire_type", ptype, ff)
        nbp = 1 if ptype == "public" and rng.random() < 0.7 else max(1, int(_logu(rng, 1, 14)))
        r.set("nb_proprietaires", nbp, ff)
        r.set("nb_parcelles", nbp + rng.randint(0, 12), Provenance("Cadastre PCI" + DEMO, "2025", "eleve"))
    forme = _wchoice(rng, [("compacte", 6), ("allongee", 2 if tf != "ferroviaire" else 8), ("morcelee", 2)])
    r.set("forme", forme, Provenance("Analyse cadastrale" + DEMO, "2025", "moyen"))
    r.set("enclave", rng.random() < 0.12, Provenance("Analyse cartographique" + DEMO, "2025", "moyen"))
    r.set("acces", _wchoice(rng, [("bon", 6), ("moyen", 3), ("difficile", 1)]), Provenance("Analyse cartographique" + DEMO, "2025", "moyen"))

    # Pollution — bases nationales
    industrial = tf in ("industrielle", "portuaire", "decharge", "logistique", "ferroviaire", "militaire")
    if rng.random() < 0.9:
        geo = Provenance("BASOL / Géorisques" + DEMO, f"20{rng.randint(19, 25)}", "eleve")
        basol = industrial and rng.random() < (0.32 if tf in ("industrielle", "decharge") else 0.12)
        r.set("basol", basol, geo)
        if basol:
            mil = ["sols"] + (["eaux_souterraines"] if rng.random() < 0.5 else [])
            r.set("basol_milieux", mil, geo)
        r.set("sis", basol and rng.random() < 0.4, Provenance("SIS / Géorisques" + DEMO, "2024", "eleve"))
        nb = 0
        if industrial and rng.random() < 0.7:
            nb = rng.randint(1, 4)
        elif tf == "commerciale" and rng.random() < 0.3:
            nb = 1  # ancienne station-service
        r.set("basias_count", nb, Provenance("BASIAS-CASIAS / Géorisques" + DEMO, str(rng.choice([1998, 2001, 2004, 2008, 2012])),
                                             "faible", "inventaire historique, non vérifié sur site"))
        icpe = industrial and rng.random() < 0.35
        r.set("icpe_count", 1 if icpe else 0, Provenance("Base ICPE / Géorisques" + DEMO, "2025-10", "eleve"))
        if icpe:
            r.set("icpe_regime", _wchoice(rng, [("autorisation", 5), ("enregistrement", 3), ("declaration", 2)]),
                  Provenance("Base ICPE / Géorisques" + DEMO, "2025-10", "eleve"))
            r.set("icpe_statut", _wchoice(rng, [("cessee", 7), ("en_activite", 2)]), Provenance("Base ICPE / Géorisques" + DEMO, "2025-10", "eleve"))
            if rng.random() < 0.08:
                r.set("seveso", rng.choice(["bas", "haut"]), Provenance("Base ICPE / Géorisques" + DEMO, "2025-10", "eleve"))

    # Réglementaire
    if rng.random() < 0.85:
        r.set("zone_humide", _wchoice(rng, [("avere", 8), ("probable", 16), ("non", 76)]),
              Provenance("Zones humides probables (INRAE)" + DEMO, "2014", "moyen"))
    r.set("captage", _wchoice(rng, [("non", 88), ("eloigne", 7), ("rapproche", 4), ("immediat", 1)]), Provenance("ARS" + DEMO, "2023", "eleve"))
    flood_prone = code in ("76540", "76231", "76498", "69091", "13004", "13078", "69123", "57672")
    r.set("ppri", _wchoice(rng, [("non", 70 if flood_prone else 88), ("bleu", 18 if flood_prone else 8), ("rouge", 12 if flood_prone else 4)]),
          Provenance("PPRi / Géorisques" + DEMO, "2022", "eleve"))
    r.set("pprt", industrial and c.get("_profil") == "port" and rng.random() < 0.25, Provenance("PPRT / Géorisques" + DEMO, "2022", "eleve"))
    r.set("natura2000", rng.random() < 0.05, Provenance("INPN" + DEMO, "2024", "eleve"))
    r.set("znieff", rng.random() < 0.1, Provenance("INPN" + DEMO, "2024", "eleve"))
    r.set("abf", dc < 1.5 and rng.random() < 0.4, Provenance("Atlas des patrimoines" + DEMO, "2024", "eleve"))
    r.set("loi_littoral", c.get("loi_littoral"), Provenance("DGALN" + DEMO, "2024", "eleve"))
    gpu = Provenance("Géoportail de l'urbanisme" + DEMO, f"20{rng.randint(19, 25)}", "eleve")
    if rng.random() < 0.88:
        zone = _wchoice(rng, [("U", 80), ("AU", 6), ("A", 4), ("N", 10)])
        r.set("plu_zone", zone, gpu)
        if zone == "U":
            voc = "activite" if industrial and rng.random() < 0.6 else _wchoice(rng, [("mixte", 4), ("habitat", 3), ("equipement", 1), ("activite", 1)])
        else:
            voc = "naturelle" if zone == "N" else "mixte"
        r.set("plu_vocation", voc, Provenance("Géoportail de l'urbanisme" + DEMO, gpu.date, "moyen"))
        r.set("servitudes_count", _wchoice(rng, [(0, 5), (1, 3), (2, 2), (3, 1)]), Provenance("SUP / GPU" + DEMO, gpu.date, "moyen"))

    # Héritage communal pour la renaturation
    r.set("ilot_chaleur", urb and dc < 2 and rng.random() < 0.5, Provenance("Cerema LCZ" + DEMO, "2023", "moyen"))
    r.set("qpv_proximite", rng.random() < (0.35 if c.get("_profil") in ("ind", "urb") else 0.15), Provenance("ANCT" + DEMO, "2024", "eleve"))
    r.set("artificialise", tf not in ("carriere",), Provenance("OCS GE" + DEMO, "2021", "moyen"))
    return r


def _non_evaluables(rng: random.Random, communes: dict[str, Record]) -> list[Record]:
    """Quelques fiches volontairement incomplètes : l'outil doit refuser de les scorer."""
    out = []
    picks = ["59512", "57227", "76351", "69259", "13055", "59172"]
    for i, code in enumerate(picks):
        c = communes[code]
        r = Record()
        r.set("id", f"DEMO-{code}-NE{i}")
        r.set("commune", c.get("commune"))
        r.set("code_insee", code)
        r.set("departement", c.get("departement"))
        r.set("epci", c.get("epci"))
        r.set("region", c.get("region"))
        p = Provenance("Cartofriches" + DEMO, "2025-02", "faible", "repérage non vérifié")
        r.set("lat", round(c.get("_lat") + rng.uniform(-0.03, 0.03), 5), p)
        r.set("lon", round(c.get("_lon") + rng.uniform(-0.03, 0.03), 5), p)
        if i % 2 == 0:
            r.set("nom", "Site repéré (surface non renseignée)", p)
            r.set("type_friche", "industrielle", p)
        else:
            r.set("nom", "Délaissé sans historique connu", p)
            r.set("surface_m2", round(rng.uniform(2000, 30000)), p)
            r.set("type_friche", "inconnu", p)
        out.append(r)
    return out


def generate(seed: int = 42) -> tuple[dict[str, Record], list[Record]]:
    rng = random.Random(seed)
    communes = commune_records(rng)
    friches = friche_records(rng, communes) + _non_evaluables(rng, communes)
    return communes, friches
