"""Catalogue des indicateurs : libellé, type, source de référence, confiance par défaut.

Sert à la fiche de vie de la donnée, à l'import CSV (colonnes attendues) et à la
saisie de données terrain. La confiance par défaut reflète la nature de la source :
BASOL / ICPE (bases administratives suivies) > Cartofriches vérifiée > BASIAS
(inventaire historique, non vérifié) > interprétation d'image.
"""

# key: (libellé, type, source de référence, confiance par défaut, valeurs possibles)
CATALOG: dict[str, tuple] = {
    # Identité / localisation
    "nom": ("Nom du site", "str", "Cartofriches", "moyen", None),
    "commune": ("Commune", "str", "Cartofriches / COG INSEE", "eleve", None),
    "code_insee": ("Code INSEE", "str", "COG INSEE", "eleve", None),
    "departement": ("Département", "str", "COG INSEE", "eleve", None),
    "epci": ("EPCI", "str", "BANATIC", "eleve", None),
    "lat": ("Latitude", "float", "Cartofriches", "moyen", None),
    "lon": ("Longitude", "float", "Cartofriches", "moyen", None),
    "surface_m2": ("Surface du site (m²)", "float", "Cartofriches / cadastre", "moyen", None),
    "type_friche": ("Type de friche", "enum", "Cartofriches", "moyen",
                    ["industrielle", "commerciale", "ferroviaire", "portuaire", "militaire", "hospitaliere",
                     "habitat", "tertiaire", "carriere", "decharge", "logistique", "agricole", "inconnu"]),
    "statut_cartofriches": ("Statut Cartofriches", "enum", "Cartofriches", "eleve", ["verifiee", "non_verifiee"]),
    "activite_passee": ("Activité passée", "str", "Cartofriches / BASIAS", "moyen", None),
    "projet_connu": ("Projet déjà identifié sur le site", "bool", "Cartofriches (statut « friche avec projet »)", "moyen", None),
    "source_inventaire": ("Origine du repérage de la friche", "str", "Cartofriches (champs source_nom / nature)", "moyen", None),
    # Bâti / foncier / géométrie
    "bati_emprise_m2": ("Emprise bâtie (m²)", "float", "BD TOPO / cadastre", "moyen", None),
    "bati_etat": ("État du bâti", "enum", "Cartofriches / visite", "faible", ["bon", "moyen", "degrade", "ruine", "inconnu"]),
    "bati_annee": ("Année de construction du bâti", "int", "Fichiers fonciers", "moyen", None),
    "siren_proprietaire": ("SIREN de la société propriétaire", "str", "Fichiers fonciers / relevé de propriété", "eleve", None),
    "siren_exploitants": ("SIREN des exploitants ICPE du site", "list", "Base ICPE / Géorisques", "eleve", None),
    "nb_proprietaires": ("Nombre de propriétaires", "int", "Fichiers fonciers (DGFiP/Cerema)", "eleve", None),
    "proprietaire_type": ("Type de propriétaire", "enum", "Fichiers fonciers", "eleve", ["public", "prive", "mixte", "inconnu"]),
    "nb_parcelles": ("Nombre de parcelles", "int", "Cadastre (PCI)", "eleve", None),
    "forme": ("Géométrie du site", "enum", "Cadastre (analyse géométrique)", "moyen", ["compacte", "allongee", "morcelee"]),
    "enclave": ("Site enclavé", "bool", "Analyse cartographique", "moyen", None),
    "acces": ("Accès", "enum", "Analyse cartographique", "moyen", ["bon", "moyen", "difficile"]),
    # Pollution
    "basol": ("Site BASOL (action publique)", "bool", "BASOL / Géorisques", "eleve", None),
    "basol_milieux": ("Milieux pollués (BASOL)", "list", "BASOL / Géorisques", "eleve", None),
    "sis": ("Secteur d'information sur les sols", "bool", "SIS / Géorisques", "eleve", None),
    "basias_count": ("Anciens sites industriels BASIAS", "int", "BASIAS-CASIAS / Géorisques", "faible", None),
    "icpe_count": ("Installations classées (ICPE)", "int", "Base ICPE / Géorisques", "eleve", None),
    "icpe_regime": ("Régime ICPE", "enum", "Base ICPE / Géorisques", "eleve", ["autorisation", "enregistrement", "declaration"]),
    "icpe_statut": ("Statut ICPE", "enum", "Base ICPE / Géorisques", "eleve", ["en_activite", "cessee"]),
    "seveso": ("Seuil SEVESO", "enum", "Base ICPE / Géorisques", "eleve", ["haut", "bas"]),
    "pollution_constatee": ("Pollution constatée (diagnostic)", "enum", "Diagnostic de sol", "eleve", ["aucune", "moderee", "forte"]),
    # Réglementaire
    "zone_humide": ("Zone humide", "enum", "Zones humides probables / inventaires", "moyen", ["avere", "probable", "non"]),
    "captage": ("Périmètre de captage", "enum", "ARS", "eleve", ["immediat", "rapproche", "eloigne", "non"]),
    "ppri": ("Zonage PPRi", "enum", "Géorisques (PPR)", "eleve", ["rouge", "bleu", "non"]),
    "pprt": ("Périmètre PPRT", "bool", "Géorisques", "eleve", None),
    "natura2000": ("Natura 2000", "bool", "INPN", "eleve", None),
    "znieff": ("ZNIEFF", "bool", "INPN", "eleve", None),
    "abf": ("Abords de monument historique", "bool", "Atlas des patrimoines", "eleve", None),
    "loi_littoral": ("Commune loi Littoral", "bool", "DGALN", "eleve", None),
    "plu_zone": ("Zone PLU(i)", "enum", "Géoportail de l'urbanisme", "eleve", ["U", "AU", "A", "N", "inconnu"]),
    "plu_vocation": ("Vocation de la zone PLU", "enum", "Géoportail de l'urbanisme", "moyen",
                     ["habitat", "mixte", "activite", "equipement", "naturelle"]),
    "servitudes_count": ("Servitudes d'utilité publique", "int", "Géoportail de l'urbanisme (SUP)", "moyen", None),
    # Desserte / localisation
    "centre_lat": ("Latitude de la mairie", "float", "API Géo", "eleve", None),
    "centre_lon": ("Longitude de la mairie", "float", "API Géo", "eleve", None),
    "distance_centre_km": ("Distance au centre-ville (km)", "float", "Calcul géographique", "eleve", None),
    "distance_gare_km": ("Distance à la gare (km)", "float", "SNCF open data", "eleve", None),
    "desserte_tc": ("Desserte transports collectifs", "enum", "GTFS / transport.data.gouv.fr", "moyen", ["forte", "moyenne", "faible"]),
    "distance_autoroute_km": ("Distance à un échangeur (km)", "float", "BD TOPO", "eleve", None),
    "distance_poste_source_km": ("Distance au poste source (km)", "float", "Enedis / Caparéseau", "moyen", None),
    "nb_arrets_tc_400m": ("Arrêts de transport collectif à moins de 400 m", "int", "OpenStreetMap (Overpass)", "moyen", None),
    "risque_inondation": ("Risque d'inondation recensé", "bool", "Géorisques (rapport de risques / GASPAR)", "moyen", None),
    "alea_argiles": ("Aléa retrait-gonflement des argiles", "enum", "Géorisques", "eleve", ["faible", "moyen", "fort"]),
    "irradiation_kwh_m2": ("Irradiation (kWh/m²/an)", "float", "PVGIS (JRC)", "eleve", None),
    # Territoire / marché
    "population": ("Population communale", "int", "INSEE recensement", "eleve", None),
    "zonage_abc": ("Zonage ABC", "enum", "Ministère du Logement", "eleve", ["Abis", "A", "B1", "B2", "C"]),
    "vacance_logement_pct": ("Vacance des logements (%)", "float", "INSEE / LOVAC", "eleve", None),
    "prix_m2_logement": ("Prix médian logement (€/m²)", "float", "DVF", "eleve", None),
    "prix_m2_dept": ("Prix médian départemental (€/m²)", "float", "DVF", "eleve", None),
    "evol_prix_3ans_pct": ("Évolution des prix sur 3 ans (%)", "float", "DVF", "moyen", None),
    "nb_mutations_3ans": ("Ventes sur 3 ans", "int", "DVF", "eleve", None),
    "prix_terrain_activite_m2": ("Prix du foncier d'activité (€/m²)", "float", "DVF (terrains)", "moyen", None),
    "prix_terrain_friche_m2": ("Prix d'acquisition friche (€/m²)", "float", "DVF / références EPF", "faible", None),
    "logements_autorises_1000hab": ("Logements autorisés / 1 000 hab. / an", "float", "SITADEL", "eleve", None),
    "vacance_tertiaire_pct": ("Vacance des bureaux (%)", "float", "Observatoire local de l'immobilier", "faible", None),
    "vacance_locaux_activite_pct": ("Vacance des locaux d'activité (%)", "float", "Observatoire local / fichiers fonciers", "faible", None),
    "demande_activite": ("Demande foncière économique", "enum", "Agence de développement / EPCI", "faible", ["forte", "moyenne", "faible"]),
    "deficit_equipements": ("Déficit d'équipements", "enum", "BPE INSEE", "moyen", ["fort", "moyen", "faible"]),
    "espaces_verts_m2_hab": ("Espaces verts (m²/hab.)", "float", "OCS GE / analyse locale", "moyen", None),
    "ilot_chaleur": ("Îlot de chaleur urbain", "bool", "Cerema LCZ", "moyen", None),
    # ZAN / politique
    "artificialise": ("Sol artificialisé", "bool", "OCS GE", "moyen", None),
    "conso_enaf_ha_2011_2021": ("Consommation d'ENAF 2011-2021 (ha)", "float", "Portail de l'artificialisation", "eleve", None),
    "programme_territorial": ("Programme ACV / PVD", "enum", "ANCT", "eleve", ["ACV", "PVD"]),
    "qpv_proximite": ("Proximité QPV", "bool", "ANCT", "eleve", None),
    "territoire_industrie": ("Territoire d'industrie", "bool", "ANCT", "eleve", None),
    "fonds_regional": ("Dispositif régional friches", "str", "Région", "moyen", None),
}


def label(key: str) -> str:
    return CATALOG.get(key, (key,))[0]


def default_confidence(key: str) -> str:
    return CATALOG.get(key, (None, None, None, "moyen"))[3]


def coerce(key: str, raw):
    """Convertit une valeur brute (CSV, formulaire) vers le type attendu."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return None
    typ = CATALOG.get(key, (None, "str"))[1]
    try:
        if typ == "float":
            return float(str(raw).replace(",", ".").replace(" ", ""))
        if typ == "int":
            return int(float(str(raw).replace(",", ".").replace(" ", "")))
        if typ == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).lower() in ("1", "true", "oui", "vrai", "yes", "o", "x")
        if typ == "list":
            if isinstance(raw, list):
                return raw
            return [s.strip() for s in str(raw).replace(";", "|").split("|") if s.strip()]
    except ValueError:
        return None
    return raw
