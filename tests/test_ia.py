"""Extraction de documents par l'IA : parcours complet avec un modèle simulé (aucun appel réseau)."""
import os
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "ia.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ia import extraction as E  # noqa: E402
from app.main import app  # noqa: E402

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"

RESULTAT = {
    "type_document": "etude_sol_pollution", "titre": "Diagnostic de pollution des sols", "date_document": "2025-03-12",
    "auteur_organisme": "Bureau d'études X", "resume": "Sondages sur 12 points. Dépassements en plomb et HCT.",
    "valeurs": [
        {"indicateur": "pollution_constatee", "valeur": "forte", "page": 14, "citation": "teneurs en plomb jusqu'à 520 mg/kg MS",
         "confiance": "eleve", "justification": "dépassements importants sur 5 sondages"},
        {"indicateur": "nb_proprietaires", "valeur": "deux", "page": 2, "citation": "deux propriétaires", "confiance": "moyen",
         "justification": ""},
        {"indicateur": "zone_humide", "valeur": "non", "page": 3, "citation": "", "confiance": "faible", "justification": ""},
    ],
    "mesures": [{"substance": "plomb", "milieu": "sols", "valeur": 520, "unite": "mg/kg MS", "valeur_reference": "fond géochimique 50",
                 "depassement": "oui", "page": 14, "citation": "520 mg/kg MS"}],
    "points_vigilance": [{"texte": "Nappe à 4 m non investiguée", "page": 20, "citation": "nappe non investiguée"}],
    "limites": "Pas d'analyse des eaux souterraines.",
}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(E, "call_model", lambda pdf, ctx: {"resultat": RESULTAT, "usage": {"entree": 40000, "sortie": 3000},
                                                           "modele": "claude-opus-5-5", "stop_reason": "end_turn"})
    with TestClient(app) as client:
        yield client


def first_site(c):
    return next(x for x in c.post("/api/scores", json={"type": "departement", "values": ["59"]}).json()["sites"] if x["evaluable"])["id"]


def wait(c, job):
    for _ in range(100):
        st = c.get(f"/api/ingest/jobs/{job['id']}").json()
        if st["etat"] != "en_cours":
            return st
        time.sleep(0.02)
    raise AssertionError("tâche non terminée")


def test_normalisation():
    res = E.normalise(RESULTAT)
    v = {x["indicateur"]: x for x in res["valeurs"]}
    assert v["pollution_constatee"]["utilisable"] and v["pollution_constatee"]["valeur_normalisee"] == "forte"
    assert not v["nb_proprietaires"]["utilisable"]          # « deux » n'est pas un entier
    assert not v["zone_humide"]["utilisable"] and "citation" in v["zone_humide"]["probleme"]  # sans preuve : non vérifiable


def test_parcours_complet(c):
    sid = first_site(c)
    assert c.get("/api/ia/statut").json()["active"]
    r = c.post(f"/api/sites/{sid}/documents", files={"file": ("etude.pdf", PDF, "application/pdf")}, data={"consentement": "false"})
    assert r.status_code == 400
    r = c.post(f"/api/sites/{sid}/documents", files={"file": ("x.pdf", b"pas un pdf", "application/pdf")}, data={"consentement": "true"})
    assert r.status_code == 422
    before = c.post(f"/api/sites/{sid}", json={"usage": "logement"}).json()["usages"]["logement"]["cost"]["total_haut"]
    job = c.post(f"/api/sites/{sid}/documents", files={"file": ("etude.pdf", PDF, "application/pdf")}, data={"consentement": "true"}).json()
    st = wait(c, job)
    assert st["etat"] == "termine" and st["rapport"]["valeurs"] == 3
    ex = c.get(f"/api/sites/{sid}/documents").json()["extractions"][0]
    assert ex["statut"] == "termine" and ex["cout_usd"] == pytest.approx(0.22)  # 40k × 4 $ + 3k × 20 $ par million
    assert c.get(f"/api/documents/{ex['id']}.pdf").content == PDF
    # rien n'est appliqué au score avant validation
    assert c.post(f"/api/sites/{sid}", json={"usage": "logement"}).json()["usages"]["logement"]["cost"]["total_haut"] == before
    r = c.post(f"/api/sites/{sid}/documents/{ex['id']}/valider",
               json={"items": [{"index": 0, "retenir": True}, {"index": 2, "retenir": False}], "auteur": "T. Lakel"}).json()
    assert r["enregistrees"] == ["pollution_constatee"]
    d = c.post(f"/api/sites/{sid}", json={"usage": "logement"}).json()
    assert d["usages"]["logement"]["cost"]["total_haut"] > before
    fv = next(x for x in d["fiche_vie"] if x["key"] == "pollution_constatee")
    assert "p. 14" in fv["source"] and "520 mg/kg" in fv["source"] and fv["date"] == "2025-03-12"
    assert c.get(f"/api/sites/{sid}/documents").json()["extractions"][0]["valide_par"] == "T. Lakel"


def test_sans_cle(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with TestClient(app) as c:
        sid = first_site(c)
        assert not c.get("/api/ia/statut").json()["active"]
        r = c.post(f"/api/sites/{sid}/documents", files={"file": ("e.pdf", PDF, "application/pdf")}, data={"consentement": "true"})
        assert r.status_code == 422 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_quota(c, monkeypatch):
    monkeypatch.setenv("FRICHESCORE_IA_QUOTA_MOIS", "0")
    sid = first_site(c)
    r = c.post(f"/api/sites/{sid}/documents", files={"file": ("e.pdf", PDF, "application/pdf")}, data={"consentement": "true"})
    assert r.status_code == 422 and "quota" in r.json()["detail"]


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def _fake_client(msg, seen):
    def stream(**kw):
        seen.update(kw)
        return _Stream(msg)
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def test_appel_api_parametres_et_refus(monkeypatch):
    import json
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}
    ok = SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                         content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(RESULTAT))])
    monkeypatch.setattr(E, "_client", lambda: _fake_client(ok, seen))
    r = E.call_model(PDF, "friche test")
    assert r["resultat"]["titre"] == RESULTAT["titre"]
    assert seen["model"] == E.MODEL and seen["output_config"]["format"]["type"] == "json_schema"
    assert seen["fallbacks"] == "default" and seen["messages"][0]["content"][0]["type"] == "document"
    refus = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
                            usage=SimpleNamespace(input_tokens=1, output_tokens=0), content=[])
    monkeypatch.setattr(E, "_client", lambda: _fake_client(refus, {}))
    with pytest.raises(E.IAError, match="refusé"):
        E.call_model(PDF, "x")
    tronque = SimpleNamespace(stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=1, output_tokens=1), content=[])
    monkeypatch.setattr(E, "_client", lambda: _fake_client(tronque, {}))
    with pytest.raises(E.IAError, match="tronquée"):
        E.call_model(PDF, "x")


def test_client_reseau_construit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert E._client() is not None  # IPv4 + TLS compact, sans appel réseau
