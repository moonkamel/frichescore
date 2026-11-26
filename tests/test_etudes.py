"""Quelle étude lancer en premier : valeur de l'information pour la décision."""
import os
import tempfile

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "e.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.scoring.etudes import etudes_possibles  # noqa: E402
from app.scoring.record import Provenance, Record  # noqa: E402

P = Provenance("test", "2026-01-01", "eleve")


def rec(**kw):
    r = Record()
    for k, v in kw.items():
        r.set(k, v, P)
    return r


def test_probabilites_a_priori_selon_les_signaux():
    basol = {e["cle"]: e for e in etudes_possibles(rec(basol=True))}["diag_sol_basol"]
    rien = {e["cle"]: e for e in etudes_possibles(rec(basol=False, basias_count=0, icpe_count=0))}["diag_sol_leger"]
    forte = lambda e: next(p for p, lbl, _ in e["resultats"] if "forte" in lbl)  # noqa: E731
    assert forte(basol) > forte(rien)
    for e in etudes_possibles(rec()):
        assert abs(sum(p for p, _, _ in e["resultats"]) - 1) < 1e-9 and e["cout_keur"][0] <= e["cout_keur"][1]
    # données déjà connues : pas d'étude proposée
    connu = rec(pollution_constatee="aucune", zone_humide="non", nb_proprietaires=1, plu_zone="U")
    assert etudes_possibles(connu) == []


def test_classement_des_etudes_et_budget():
    with TestClient(app) as c:
        r = c.post("/api/etudes", json={"perimeter": {"type": "departement", "values": ["59"]}, "top_n": 10, "budget_keur": 30}).json()
        assert r["seuil"] and r["etudes"]
        probs = [e["p_changement"] for e in r["etudes"]]
        assert probs == sorted(probs, reverse=True) and probs[0] > 0
        for e in r["etudes"]:
            # cohérence : la probabilité est la somme des résultats qui changent la décision
            assert abs(e["p_changement"] - sum(x["probabilite"] for x in e["resultats"] if x["change_decision"])) < 0.02
            assert e["fourchette_score"][0] <= e["fourchette_score"][1]
        assert r["cout_selection_keur"] <= 30 and all(s["p_changement"] > 0 for s in r["selection"])
        # un périmètre plus petit que le top N : rien à départager
        one = next(iter(db.load_sites({"type": "departement", "values": ["59"]})))
        m = c.post("/api/etudes", json={"perimeter": {"type": "ids", "values": [one]}, "top_n": 10}).json()
        assert m["etudes"] == [] and "aucune étude" in m["message"]


def test_cout_des_etudes_parametrable():
    with TestClient(app) as c:
        base = c.post("/api/etudes", json={"perimeter": {"type": "departement", "values": ["59"]}, "top_n": 10}).json()
        e = next(x for x in base["etudes"] if x["cle"] == "plu")
        c.put("/api/parametres", json={"changes": [{"path": "ETUDES_COUT_KEUR.plu", "value": [10, 20]}]})
        try:
            after = c.post("/api/etudes", json={"perimeter": {"type": "departement", "values": ["59"]}, "top_n": 10}).json()
            e2 = next(x for x in after["etudes"] if x["cle"] == "plu" and x["site"] == e["site"])
            assert e2["cout_keur"] == [10, 20] and e2["efficacite"] < e["efficacite"] or e["p_changement"] == 0
        finally:
            c.post("/api/parametres/reset")
