"""Tests du moteur FricheScore : ils verrouillent les règles de conception produit."""
from datetime import date

import pytest

from app.demo.generate import generate
from app.scoring import config as C
from app.scoring.compare import compare, extension_site
from app.scoring.engine import best_usage, check_evaluable, evaluate_site, evaluate_usage, global_score
from app.scoring.record import Provenance, Record
from app.scoring.stress import monte_carlo, run_scenario

TODAY = date(2026, 11, 26)
W = C.PROFILES["equilibre"]["weights"]


def site(**over) -> Record:
    p = Provenance("test", "2025", "eleve")
    base = dict(id="T1", nom="Test", code_insee="59350", commune="Lille", lat=50.6, lon=3.0, surface_m2=20000,
                type_friche="industrielle", basol=False, basias_count=0, icpe_count=0, sis=False,
                bati_emprise_m2=4000, bati_etat="moyen", bati_annee=2000, nb_proprietaires=1, proprietaire_type="prive",
                forme="compacte", enclave=False, acces="bon", zone_humide="non", captage="non", ppri="non", pprt=False,
                natura2000=False, znieff=False, abf=False, loi_littoral=False, plu_zone="U", plu_vocation="mixte",
                servitudes_count=0, distance_centre_km=1.5, distance_gare_km=1, desserte_tc="forte",
                distance_autoroute_km=5, distance_poste_source_km=5, irradiation_kwh_m2=1300, population=100000,
                zonage_abc="B1", vacance_logement_pct=7, prix_m2_logement=3000, prix_m2_dept=2500,
                evol_prix_3ans_pct=4, nb_mutations_3ans=800, prix_terrain_activite_m2=50, prix_terrain_friche_m2=20,
                logements_autorises_1000hab=6, vacance_tertiaire_pct=8, vacance_locaux_activite_pct=8,
                demande_activite="moyenne", deficit_equipements="moyen", espaces_verts_m2_hab=12, ilot_chaleur=False,
                artificialise=True, conso_enaf_ha_2011_2021=40, qpv_proximite=False, territoire_industrie=False)
    base.update(over)
    r = Record()
    for k, v in base.items():
        if v is not None:
            r.set(k, v, p)
    return r


def g(rec, usage="logement", hyp=None, weights=W):
    return global_score(evaluate_usage(rec, usage, hyp, TODAY), weights)


# ------------------------------------------------------------ évaluabilité
def test_refuse_sans_surface():
    r = site()
    del r.values["surface_m2"]
    assert "surface du site" in check_evaluable(r)
    assert evaluate_site(r)["evaluable"] is False


def test_refuse_sans_pollution_ni_usage_passe():
    r = site(type_friche="inconnu")
    for k in ("basol", "basias_count", "icpe_count", "sis"):
        del r.values[k]
    assert check_evaluable(r)


def test_usage_passe_suffit():
    r = site()
    for k in ("basol", "basias_count", "icpe_count", "sis"):
        del r.values[k]
    assert check_evaluable(r) == []


# ------------------------------------------------------------ dimensions
def test_cinq_dimensions_et_trois_facteurs():
    res = g(site())
    ur = evaluate_usage(site(), "logement", None, TODAY)
    assert set(ur["dims"]) == {"d1", "d2", "d3", "d4", "d5"}
    assert all(0 <= v <= 100 for v in ur["dims"].values())
    assert len(res["drivers"]) == 3


def test_pollution_basol_degrade_cout():
    clean = evaluate_usage(site(), "logement", None, TODAY)
    dirty = evaluate_usage(site(basol=True, basol_milieux=["sols", "eaux_souterraines"]), "logement", None, TODAY)
    assert dirty["cost"]["total_haut"] > clean["cost"]["total_haut"] * 2
    assert dirty["dims"]["d2"] < clean["dims"]["d2"] - 15
    assert "BASOL" in dirty["cost"]["phrase"]


def test_sensibilite_usage_pollution():
    r = site(basol=True, basol_milieux=["sols", "eaux_souterraines"])
    log = evaluate_usage(r, "logement", None, TODAY)["cost"]
    pv = evaluate_usage(r, "photovoltaique", None, TODAY)["cost"]
    dep = lambda c: sum(p["haut"] for p in c["postes"] if p["categorie"] == "pollution")
    assert dep(pv) < dep(log) * 0.5


def test_donnee_terrain_prime_sur_bases():
    r = site(basol=True, basol_milieux=["sols"])
    worst = evaluate_usage(r, "logement", None, TODAY)["dims"]["d2"]
    r.set("pollution_constatee", "aucune", Provenance("Diagnostic", "2026-10", "eleve"))
    assert evaluate_usage(r, "logement", None, TODAY)["dims"]["d2"] > worst + 10


def test_friction_penalise_global():
    a = g(site())
    b = g(site(zone_humide="avere", natura2000=True, plu_zone="N", plu_vocation="naturelle"))
    assert b["global"] < a["global"] - 10
    fr = evaluate_usage(site(zone_humide="avere"), "logement", None, TODAY)["friction"]
    assert fr["delai_total_mois"] > C.BASE_DELAY_MONTHS


def test_usage_redhibitoire_plafonne():
    r = site(ppri="rouge")
    res = g(r, "logement")
    assert res["bloque"] and res["global"] <= C.BLOCKER_CAP
    assert not g(r, "photovoltaique")["bloque"]


def test_usages_differencies():
    r = site(surface_m2=80000, irradiation_kwh_m2=1600, distance_centre_km=9, desserte_tc="faible", zonage_abc="C")
    d1 = {u: evaluate_usage(r, u, None, TODAY)["dims"]["d1"] for u in C.USAGES}
    assert d1["photovoltaique"] > d1["logement"] + 20


# ------------------------------------------------------------ confiance
def test_donnees_anciennes_penalisees():
    fresh = site()
    old = site()
    for k in old.values:
        old.prov[k] = Provenance("vieille base", "1998", "faible")
    a, b = g(fresh), g(old)
    assert b["penalite"] > a["penalite"] + 5
    assert b["global"] < a["global"]
    assert any(d["dim"] == "conf" for d in b["drivers"])


def test_donnee_manquante_reduit_confiance():
    r = site()
    del r.values["plu_zone"]
    assert g(r)["confiance"] < g(site())["confiance"]


def test_seveso_absent_ne_penalise_pas():
    ur = evaluate_usage(site(), "logement", None, TODAY)
    keys = [w["key"] for ws in ur["weak"].values() for w in ws]
    assert "seveso" not in keys


# ------------------------------------------------------------ pondérations
def test_profils_changent_classement():
    cheap_slow = site(zone_humide="avere", natura2000=True)
    fast_dear = site(basol=True, basol_milieux=["sols", "eaux_souterraines"], bati_etat="ruine")
    rap = C.PROFILES["rapidite"]["weights"]
    ren = C.PROFILES["rentabilite"]["weights"]
    assert g(fast_dear, weights=rap)["global"] > g(cheap_slow, weights=rap)["global"]
    assert g(cheap_slow, weights=ren)["global"] > g(fast_dear, weights=ren)["global"]


def test_d5_desactivable():
    ur = evaluate_usage(site(), "logement", None, TODAY)
    on = global_score(ur, W, True)
    off = global_score(ur, W, False)
    assert on["global"] != off["global"]


# ------------------------------------------------------------ contre-analyse
def test_scenario_depollution_degrade():
    r = site(basol=True, basol_milieux=["sols"])
    assert g(r, hyp={"cout_depollution_mult": 2})["global"] < g(r)["global"]


def test_scenario_et_monte_carlo_sur_demo():
    communes, friches = generate()
    sites = {}
    for f in friches[:80]:
        c = communes[f.get("code_insee")]
        base = Record({k: v for k, v in c.values.items() if not k.startswith("_")}, dict(c.prov))
        sites[f.get("id")] = base.merged_with(f)
    res = run_scenario(sites, None, W, True, {"cout_depollution_mult": 2, "delai_supplementaire_mois": 12}, 10)
    assert len(res["lignes"]) == 10
    assert {l["statut"] for l in res["lignes"]} <= {"stable", "fragile", "sort du top", "s'effondre"}
    mc = monte_carlo(sites, "logement", W, True, {}, 10, runs=20)
    assert all(0 <= l["p_top"] <= 1 for l in mc["lignes"])


# ------------------------------------------------------------ jumeau
def test_comparaison_friche_vs_extension():
    ext = extension_site({"surface_m2": 30000, "plu_zone": "AU"}, None)
    for k in ("population", "zonage_abc", "prix_m2_logement", "prix_m2_dept"):
        ext.set(k, site().get(k), Provenance("t", "2025", "eleve"))
    res = compare((site(), "logement"), (ext, "logement"), W)
    assert len(res["synthese"]) == 3
    assert res["a"]["dims"]["d5"] > res["b"]["dims"]["d5"]  # la friche préserve le budget ZAN
    assert "ZAN" in res["synthese"][2]


# ------------------------------------------------------------ démo
def test_demo_deterministe_et_non_evaluables():
    c1, f1 = generate(42)
    c2, f2 = generate(42)
    assert [f.get("id") for f in f1] == [f.get("id") for f in f2]
    assert sum(1 for f in f1 if check_evaluable(f)) >= 3
    assert all("(démo)" in p.source for f in f1[:20] for k, p in f.prov.items())


def test_best_usage_evite_redhibitoire():
    r = site(ppri="rouge")
    u, res = best_usage(evaluate_site(r, today=TODAY), W)
    assert not res["bloque"]


@pytest.mark.parametrize("usage", list(C.USAGES))
def test_tous_usages_bornes(usage):
    ur = evaluate_usage(site(), usage, None, TODAY)
    assert 0 <= global_score(ur, W)["global"] <= 100
    assert ur["cost"]["total_bas"] <= ur["cost"]["total_haut"]
