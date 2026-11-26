"""Tests de l'API de bout en bout (base SQLite temporaire, jeu de démo)."""
import os
import tempfile

os.environ["FRICHESCORE_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ingest.connectors import cartofriches_records, dvf_indicators, parse_cartofriches  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def first_eval(c, dep="59"):
    s = c.post("/api/scores", json={"type": "departement", "values": [dep]}).json()["sites"]
    return [x for x in s if x["evaluable"]][0]["id"], s


def test_meta(c):
    m = c.get("/api/meta").json()
    assert len(m["usages"]) == 6 and len(m["profiles"]) >= 4


def test_scores_et_non_evaluables(c):
    sid, s = first_eval(c)
    assert any(not x["evaluable"] and x["manquants"] for x in s)
    site = next(x for x in s if x["id"] == sid)
    assert set(site["usages"]) == {"logement", "activite", "tertiaire", "photovoltaique", "renaturation", "equipement"}


def test_detail_fiche_de_vie_et_contestation(c):
    sid, _ = first_eval(c)
    d = c.post(f"/api/sites/{sid}", json={"usage": "logement"}).json()
    assert d["fiche_vie"] and all("source" in x and "confiance" in x for x in d["fiche_vie"])
    w = c.post(f"/api/sites/{sid}", json={"usage": "logement", "overrides": {"pollution_constatee": "forte"}}).json()
    assert w["globals"]["logement"]["global"] < d["globals"]["logement"]["global"]


def test_portfolio_terrain_historique(c):
    sid, _ = first_eval(c)
    assert c.put(f"/api/portfolio/{sid}", json={"stage": "reperee"}).status_code == 200
    r = c.post(f"/api/sites/{sid}/terrain", json={"key": "pollution_constatee", "value": "aucune", "source": "Diagnostic"})
    assert r.status_code == 200
    assert c.post(f"/api/sites/{sid}/terrain", json={"key": "zone_humide", "value": "peut-être"}).status_code == 422
    items = c.post("/api/portfolio/list", json={}).json()["items"]
    it = next(i for i in items if i["id"] == sid)
    assert len(it["history"]) >= 2 and it["n_terrain"] == 1
    d = c.post(f"/api/sites/{sid}", json={}).json()
    assert next(x for x in d["fiche_vie"] if x["key"] == "pollution_constatee")["source"] == "Diagnostic"


def test_stress_mc_heat_zan_compare(c):
    per = {"type": "departement", "values": ["59"]}
    assert c.post("/api/stress", json={"perimeter": per, "preset": "defavorable"}).json()["lignes"]
    assert c.post("/api/montecarlo", json={"perimeter": per, "runs": 20}).json()["lignes"]
    h = c.post("/api/heatmap?level=epci", json={"perimeter": per}).json()
    assert h["groupes"] and h["points"]
    z = c.post("/api/zan", json={"perimeter": per}).json()
    assert z["jalons"] and "correspondances" in z
    sid, _ = first_eval(c)
    r = c.post("/api/compare", json={"a": {"id": sid, "usage": "logement"},
                                     "b": {"usage": "logement", "extension": {"code_insee": "59350", "surface_m2": 20000}}}).json()
    assert len(r["synthese"]) == 3


def test_exports(c):
    sid, _ = first_eval(c)
    assert c.post(f"/api/sites/{sid}/pdf", json={}).content[:4] == b"%PDF"
    r = c.post("/api/export/rapport", json={"perimeter": {"type": "departement", "values": ["57"]}, "top_n": 5})
    assert r.content[:4] == b"%PDF"
    csv = c.post("/api/export/csv", json={}).text
    assert csv.splitlines()[0].lstrip("﻿").startswith("rang;id;nom")


def test_import_csv(c):
    data = ("site_id;site_nom;comm_insee;comm_nom;latitude;longitude;site_surface;site_type;basias_count\n"
            "X1;Usine test;59350;Lille;50.6;3.05;12000;friche industrielle;2\n"
            "X2;Sans surface;59350;Lille;50.6;3.05;;friche industrielle;\n")
    r = c.post("/api/import/csv", files={"file": ("t.csv", data.encode(), "text/csv")}, data={"source": "test"}).json()
    assert r["importees"] == 2
    s = c.post("/api/scores", json={"type": "ids", "values": ["X1", "X2"]}).json()["sites"]
    by = {x["id"]: x for x in s}
    assert by["X1"]["evaluable"] and not by["X2"]["evaluable"]


def test_parsers_connecteurs():
    rows = parse_cartofriches({"features": [{"properties": {"site_id": "59350_1", "site_nom": "A", "comm_insee": "59350",
                                                            "site_surface": 5000, "site_type": "friche industrielle",
                                                            "l_basias": "NPC1|NPC2"},
                                             "geometry": {"type": "Point", "coordinates": [3.0, 50.6]}}]})
    recs, _w = cartofriches_records(rows)
    assert recs[0].get("id") == "CF-59350_1" and recs[0].get("basias_count") == 2 and recs[0].get("lat") == 50.6
    assert recs[0].prov["basias_count"].confiance == "faible"
    csv1 = ("id_mutation,nature_mutation,valeur_fonciere,type_local,surface_reelle_bati\n"
            "1,Vente,200000,Appartement,50\n2,Vente,300000,Maison,100\n")
    csv2 = "id_mutation,nature_mutation,valeur_fonciere,type_local,surface_reelle_bati\n3,Vente,240000,Appartement,50\n"
    out = dvf_indicators({2022: csv1, 2024: csv2})
    assert out["prix_m2_logement"] == 4800 and out["evol_prix_3ans_pct"] > 0


def test_friche_reelle_n_herite_pas_de_la_demo(c):
    from app import db
    from app.scoring.record import Provenance, Record
    r = Record()
    p = Provenance("API Cartofriches (Cerema)", "2026-11-26", "moyen")
    for k, v in {"id": "CF-TEST-1", "nom": "Réelle", "code_insee": "59350", "departement": "59", "surface_m2": 500,
                 "lat": 50.6, "lon": 3.05, "activite_passee": "BASIAS"}.items():
        r.set(k, v, p)
    db.upsert_friche(r, "cartofriches")
    site = db.load_sites({"type": "ids", "values": ["CF-TEST-1"]})["CF-TEST-1"]
    assert not any(pr.source.endswith("(démo)") for pr in site.prov.values())
    assert not site.has("zonage_abc")  # indicateur communal de démo non hérité
    demo = next(iter(db.load_sites({"type": "communes", "values": ["59350"]}).values()))
    assert demo.has("zonage_abc") or demo.get("_origine") != "demo"


def test_import_perimetre_en_tache_de_fond(c, monkeypatch):
    import time
    from app.ingest import connectors as K
    from app.scoring.record import Provenance, Record

    def fake_fetch(code, base_url=None):
        out = []
        for i in range(12):
            r = Record()
            p = Provenance("API Cartofriches (Cerema)", "2026-11-26", "moyen")
            for k, v in {"id": f"CF-{code}_{i}", "nom": f"Site {i}", "code_insee": code, "departement": code[:2],
                         "surface_m2": 3000 + i, "lat": 50.6, "lon": 3.05, "activite_passee": "BASIAS"}.items():
                r.set(k, v, p)
            out.append(r)
        return out, []

    def fake_geo(rec, rayon_m=None, base_url=None):
        rec.set("basias_count", 1, Provenance("CASIAS", "2026-11-26", "faible"))
        return rec, []

    monkeypatch.setattr(K, "fetch_cartofriches", fake_fetch)
    monkeypatch.setattr(K, "enrich_georisques", fake_geo)
    monkeypatch.setattr(K, "fetch_dvf", lambda code, years, base_url=None: ({"prix_m2_logement": 3500, "_annees": [2024]}, []))
    job = c.post("/api/ingest/cartofriches", json={"codes_insee": ["59350", "59512"]}).json()
    for _ in range(100):
        st = c.get(f"/api/ingest/jobs/{job['id']}").json()
        if st["etat"] != "en_cours":
            break
        time.sleep(0.05)
    assert st["etat"] == "termine", st
    assert st["rapport"]["importees"] == 24 and st["rapport"]["communes_dvf"] == 2
    s = c.post("/api/scores", json={"type": "ids", "values": ["CF-59350_0", "CF-59512_11"]}).json()["sites"]
    assert len(s) == 2 and all(x["evaluable"] for x in s)
    assert c.get("/api/ingest/jobs/inconnu").status_code == 404
