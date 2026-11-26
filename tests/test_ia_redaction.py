"""Rédacteur IA (simulé) : dossier transmis, contrôle des chiffres, note dans la fiche et le PDF, jumeau."""
import os
import tempfile

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "r.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import service  # noqa: E402
from app.ia import redaction as RD  # noqa: E402
from app.main import app  # noqa: E402


def test_controle_des_chiffres():
    dossier = {"remise_en_etat": {"fourchette_eur": [1_250_000, 3_400_000]}, "frichescore": {"global": 66.2},
               "friction": {"delai_total_mois": 19}, "texte": "zone A, 238 246 habitants"}
    note = {"synthese": ["Coût de remise en état entre 1,25 et 3,4 M€, délai d'environ 19 mois, score de 66.",
                         "La commune compte 238 246 habitants ; un bilan de 7,5 M€ est attendu en 2027."]}
    assert RD.unverified_numbers(note, dossier) == ["7,5 M€"]


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}

    def fake(system, content, schema, effort="medium", max_tokens=16000, client=None):
        seen["content"], seen["schema"] = content, schema
        if "conclusion" in schema["properties"]:
            return {"resultat": {"conclusion": ["A est préférable.", "Écart de friction.", "Vigilance sur la pollution."],
                                 "nuances": ["B a un meilleur marché"], "conditions_de_bascule": ["si la dépollution double"]},
                    "usage": {"entree": 3000, "sortie": 500}, "modele": "claude-opus-5-5"}
        return {"resultat": {"titre": "Site prioritaire pour une étude", "synthese": ["Paragraphe 1.", "Score de 999 points."],
                             "points_forts": ["Desserte"], "points_vigilance": ["Pollution"], "prochaine_etape": "Diagnostic de sol",
                             "fiabilite": "Confiance moyenne."},
                "usage": {"entree": 4000, "sortie": 800}, "modele": "claude-opus-5-5"}
    monkeypatch.setattr(RD, "run_structured", fake)
    with TestClient(app) as client:
        client.seen = seen
        yield client


def first_site(c):
    return next(x for x in c.post("/api/scores", json={"type": "departement", "values": ["59"]}).json()["sites"] if x["evaluable"])["id"]


def test_note_site_parcours(c):
    sid = first_site(c)
    n = c.post(f"/api/sites/{sid}/note", json={"usage": "logement"}).json()
    assert n["note"]["titre"].startswith("Site prioritaire") and n["usage"] == "logement"
    assert n["note"]["chiffres_a_verifier"] == ["999"]  # chiffre absent du dossier : signalé
    assert '"frichescore"' in c.seen["content"] and '"etudes_recommandees"' in c.seen["content"]
    d = c.post(f"/api/sites/{sid}", json={"usage": "logement"}).json()
    assert d["note_ia"]["note"]["prochaine_etape"] == "Diagnostic de sol"
    pdf = c.post(f"/api/sites/{sid}/pdf", json={"usage": "logement"})
    assert pdf.content[:4] == b"%PDF" and len(pdf.content) > 1000
    # la note d'un usage n'est pas affichée pour un autre usage
    assert c.post(f"/api/sites/{sid}", json={"usage": "activite"}).json()["note_ia"] is None


def test_dossier_ne_contient_que_les_donnees_du_site(c):
    sid = first_site(c)
    d = service.site_detail(sid, None, True, "logement")
    dos = RD.dossier_site(d, "logement")
    assert set(dos) >= {"site", "frichescore", "dimensions", "remise_en_etat", "friction", "etudes_recommandees"}
    assert dos["frichescore"]["global"] == d["globals"]["logement"]["global"]


def test_conclusion_jumeau(c):
    s = [x["id"] for x in c.post("/api/scores", json={"type": "departement", "values": ["59"]}).json()["sites"] if x["evaluable"]][:2]
    r = c.post("/api/ia/jumeau", json={"a": {"id": s[0], "usage": "logement"}, "b": {"id": s[1], "usage": "activite"}}).json()
    assert len(r["conclusion"]) == 3 and r["conditions_de_bascule"] and r["cout_usd"] > 0
    assert '"site_A"' in c.seen["content"]


def test_note_sans_cle(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with TestClient(app) as c:
        sid = first_site(c)
        assert c.post(f"/api/sites/{sid}/note", json={}).status_code == 422
