"""Paramètres du FricheScore.

Toutes les hypothèses chiffrées sont regroupées ici pour être lues, discutées et
modifiées. Ce sont des ORDRES DE GRANDEUR destinés à hiérarchiser des sites, pas
des prix de devis. Les fourchettes de coûts unitaires sont inspirées des
fourchettes publiques couramment citées (guides ADEME / Cerema sur la
reconversion des friches, retours d'expérience EPF) et doivent être calibrées
par chaque utilisateur sur ses propres opérations.
"""

USAGES = {
    "logement": "Logement",
    "activite": "Industrie / logistique",
    "tertiaire": "Tertiaire",
    "photovoltaique": "Photovoltaïque au sol",
    "renaturation": "Renaturation",
    "equipement": "Équipement public",
}

DIMENSIONS = {
    "d1": "Potentiel d'usage",
    "d2": "Coût de remise en état",
    "d3": "Friction réglementaire",
    "d4": "Attractivité économique",
    "d5": "Impact & financements",
}

# Pondérations : d3 entre dans le global sous la forme (100 - friction).
PROFILES = {
    "equilibre": {"label": "Équilibré", "weights": {"d1": 25, "d2": 25, "d3": 20, "d4": 20, "d5": 10}},
    "rentabilite": {"label": "Rentabilité", "weights": {"d1": 15, "d2": 35, "d3": 10, "d4": 40, "d5": 0}},
    "rapidite": {"label": "Rapidité", "weights": {"d1": 15, "d2": 25, "d3": 45, "d4": 15, "d5": 0}},
    "impact": {"label": "Impact", "weights": {"d1": 15, "d2": 15, "d3": 10, "d4": 10, "d5": 50}},
}

CONFIDENCE_MAX_PENALTY = 20  # points retirés au global pour une confiance nulle
BLOCKER_CAP = 10  # plafond du score global lorsqu'un usage est rédhibitoire

# Sensibilité de l'usage au niveau de dépollution exigé (méthodologie
# nationale de gestion des sites pollués : la dépollution dépend de l'usage futur).
USAGE_POLLUTION_SENSITIVITY = {
    "logement": 1.0,
    "equipement": 1.05,  # établissements sensibles (crèches, écoles)
    "tertiaire": 0.7,
    "activite": 0.5,
    "renaturation": 0.45,
    "photovoltaique": 0.25,  # confinement / maintien en place le plus souvent
}

# Coûts unitaires (€/m²) : (bas, haut)
COST = {
    "demolition_m2_emprise": (50, 150),
    "desamiantage_m2_emprise": (20, 80),
    "desimpermeabilisation_m2": (30, 80),
    "viabilisation": {
        "logement": (25, 60),
        "tertiaire": (20, 50),
        "equipement": (20, 50),
        "activite": (10, 30),
        "photovoltaique": (3, 10),
        "renaturation": (5, 20),
    },
    "raccordement_pv_eur_km": (60_000, 120_000),
    # Dépollution : (coût bas, coût haut, fraction bas, fraction haute du site concernée)
    "pollution": {
        "basol_eaux": (60, 250, 0.3, 0.7),
        "basol_sols": (40, 150, 0.2, 0.5),
        "sis": (40, 150, 0.2, 0.5),
        "icpe_seveso": (50, 200, 0.2, 0.6),
        "icpe_cessee": (20, 100, 0.1, 0.3),
        "basias": (10, 80, 0.1, 0.4),
        "terrain_forte": (80, 250, 0.4, 0.7),
        "terrain_moderee": (15, 60, 0.2, 0.4),
        "terrain_aucune": (0, 5, 0.0, 1.0),
        "inconnue": (0, 60, 0.0, 0.4),
    },
    "foncier_multi_proprio_m2": (5, 15),
    "extension_reseaux_m2": (15, 40),
    "fondations_argiles_fort_m2": (5, 15),  # surcoût de fondations, aléa retrait-gonflement fort (usages bâtis)
}

# Friction réglementaire : (points de friction, mois de délai) par contrainte
FRICTION = {
    "zone_humide_avere": (25, 12),
    "zone_humide_probable": (12, 6),
    "zone_humide_non_verifiee": (4, 2),
    "captage_immediat": (40, 24),
    "captage_rapproche": (20, 6),
    "captage_eloigne": (6, 2),
    "ppri_rouge": (30, 6),
    "ppri_bleu": (10, 3),
    "pprt": (15, 6),
    "natura2000": (15, 6),
    "natura2000_renaturation": (3, 6),
    "znieff": (6, 4),
    "abf": (8, 3),
    "loi_littoral": (10, 6),
    "etude_impact_systematique": (15, 12),
    "etude_impact_cas_par_cas": (6, 3),
    "pv_etude_impact_systematique": (12, 10),
    "pv_cas_par_cas": (5, 3),
    "plu_non_renseigne": (8, 6),
    "plu_modification": (10, 10),
    "plu_revision": (25, 24),
    "servitude_par_unite": (3, 2),
    "icpe_en_activite": (15, 12),
    "icpe_cessee": (6, 4),
    "seveso": (10, 6),
    "proprietaires_plus_de_5": (12, 12),
    "proprietaires_3_a_5": (6, 6),
    "proprietaires_inconnus": (4, 3),
    "extension_zan": (12, 6),
    "risque_inondation_sans_ppri": (6, 3),  # risque recensé par Géorisques, zonage du PPRi non renseigné
}
FRICTION_POINTS_PAR_MOIS_HYPOTHESE = 1.2  # contre-analyse : points de friction par mois de délai ajouté

# Seuils d'évaluation environnementale (annexe R.122-2), en hectares / MWc
SEUIL_ETUDE_IMPACT_HA = 10
SEUIL_CAS_PAR_CAS_HA = 5
SEUIL_PV_ETUDE_IMPACT_MWC = 1.0
SEUIL_PV_CAS_PAR_CAS_MWC = 0.3

# Délais (mois) liés aux procédures, utilisés par la dimension friction
BASE_DELAY_MONTHS = 15  # études pré-opérationnelles + autorisations, sans complication

# Échelles de normalisation
COST_SCALE_EUR_M2 = 250  # coût médian au-delà duquel le risque de coût est maximal
UNCERTAINTY_SCALE_EUR_M2 = 200  # largeur de fourchette au-delà de laquelle l'incertitude est maximale

# Seuils de qualification
PIEGEUX_D2_MAX = 40  # D2 < 40 => site « piégeux » (coûts cachés probables)
PEPITE_D2_MIN = 65
PEPITE_FRICTION_MAX = 30
PEPITE_CONF_MIN = 0.7
