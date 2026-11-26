"""Contre-analyse en langage naturel : l'IA (simulée) traduit, le moteur calcule."""
import os
import tempfile

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "q.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ia import client as IC  # noqa: E402
from app.ia import questions as Q  # noqa: E402
from app.main import app  # noqa: E402
from app.scoring.engine import evaluate_usage  # noqa: E402
from app.scoring.record import Provenance  # noqa: E402
from tests.test_scoring import site  # noqa: E402


# ------------------------------------------------------------------ leviers du moteur
def test_levier_subvention():
    r = site(prix_m2_logement=1500, basol=True, basol_milieux=["sols", "eaux_souterraines"])
    a = evaluate_usage(r, "logement")
    b = evaluate_usage(r, "logement", {"subvention_eur": 500_000})
    assert b["market"]["bilan_net_bas"] == a["market"]["bilan_net_bas"] + 500_000
    c = evaluate_usage(r, "logement", {"subvention_eur": 500_000, "sans_subventions": True})
    assert c["market"]["bilan_net_bas"] == a["market"]["bilan_net_bas"]
    # « éligibles » : un site au bilan positif ne reçoit pas la subvention, sauf cible « tous »
    riche = site(prix_m2_logement=9000)
    base = evaluate_usage(riche, "logement")["market"]["bilan_net_bas"]
    assert base > 0
    assert evaluate_usage(riche, "logement", {"subvention_eur": 1e5})["market"]["bilan_net_bas"] == base
    assert evaluate_usage(riche, "logement", {"subvention_eur": 1e5, "subvention_cible": "tous"})["market"]["bilan_net_bas"] == base + 1e5


def test_levier_compensation_zone_humide():
    r = site()
    a = evaluate_usage(r, "logement")
    tous = evaluate_usage(r, "logement", {"compensation_zone_humide": "tous"})
    assert tous["dims"]["d3"] > a["dims"]["d3"] and tous["cost"]["total_haut"] > a["cost"]["total_haut"]
    concernes = evaluate_usage(r, "logement", {"compensation_zone_humide": "concernes"})
    assert concernes["dims"]["d3"] == a["dims"]["d3"]  # zone_humide = non : pas concerné
    r.set("zone_humide", "probable", Provenance("t", "2026", "moyen"))
    assert evaluate_usage(r, "logement", {"compensation_zone_humide": "concernes"})["cost"]["total_haut"] > \
        evaluate_usage(r, "logement")["cost"]["total_haut"]


# ------------------------------------------------------------------ traduction
def test_clean_bornes_et_leviers_actifs():
    out = Q.clean({"interpretation": "x", "modelisable": True, "top_n": 200,
                   "hypotheses": {**Q.NEUTRE, "cout_depollution_mult": 12, "prix_marche_pct": -20, "subvention_cible": "tous"},
                   "hypotheses_supposees": [], "non_modelisable": []})
    assert out["hypotheses"] == {"cout_depollution_mult": 4, "prix_marche_pct": -20}  # borne + cible sans montant ignorée
    assert out["top_n"] == 50


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}

    def fake(system, content, schema, effort="medium", max_tokens=16000, client=None):
        seen.update(system=system, content=content, schema=schema)
        return {"resultat": {"interpretation": "La mairie exige une zone humide compensatoire et une subvention de 500 k€ est obtenue.",
                             "modelisable": True,
                             "hypotheses": {**Q.NEUTRE, "compensation_zone_humide": "tous", "subvention_eur": 500000},
                             "hypotheses_supposees": ["compensation exigée sur tous les sites"],
                             "non_modelisable": ["risque de recours contentieux"], "top_n": 5},
                "usage": {"entree": 2000, "sortie": 400}, "modele": "claude-opus-5-5"}
    monkeypatch.setattr(Q, "run_structured", fake)
    with TestClient(app) as client:
        client.seen = seen
        yield client


def test_question_parcours(c):
    r = c.post("/api/ia/question", json={"question": "Et si la mairie exigeait une zone humide compensatoire, avec 500 k€ de Fonds Vert ?",
                                          "perimeter": {"type": "departement", "values": ["59"]}, "perimeter_label": "Nord"}).json()
    it = r["interpretation"]
    assert it["hypotheses"] == {"subvention_eur": 500000, "compensation_zone_humide": "tous"}
    assert r["scenario"]["top_n"] == 5 and len(r["scenario"]["lignes"]) == 5
    assert "recours" in it["non_modelisable"][0]
    assert r["cout_usd"] == pytest.approx(IC.cout_usd({"entree": 2000, "sortie": 400}))
    assert "Nord" in c.seen["content"] and "compensation_zone_humide" in c.seen["system"]
    st = c.get("/api/ia/statut").json()
    assert st["autres_appels"] >= 1


def test_question_trop_courte(c):
    assert c.post("/api/ia/question", json={"question": "?"}).status_code == 422


def test_question_sans_cle(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with TestClient(app) as c:
        r = c.post("/api/ia/question", json={"question": "Et si les prix baissaient de 20 % ?"})
        assert r.status_code == 422 and "ANTHROPIC_API_KEY" in r.json()["detail"]
