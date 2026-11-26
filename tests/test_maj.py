"""Mise à jour automatique (planificateur, journal, écarts de score) et packs de données par département."""
import os
import tempfile
from datetime import datetime

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "m.db"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, maj, service  # noqa: E402
from app.ingest import connectors as K  # noqa: E402
from app.ingest import territoire  # noqa: E402
from app.main import app  # noqa: E402
from app.scoring.record import Provenance  # noqa: E402

ORIG = "cartofriches"


def _real(sid, code="59350", origine=ORIG):
    """Copie d'une friche de démonstration évaluable, présentée comme une friche réelle."""
    base = next(r for k, r in db.load_sites({"type": "departement", "values": ["59"]}).items()
                if str(r.get("_origine")).startswith("demo") and service.site_detail(k, None, True)["evaluable"])
    rec = base.copy()
    rec.values.pop("_origine", None)
    rec.set("id", sid, Provenance("Cartofriches", "2026-01-01", "eleve"))
    rec.set("code_insee", code, Provenance("Cartofriches", "2026-01-01", "eleve"))
    rec.set("departement", code[:2], Provenance("Cartofriches", "2026-01-01", "eleve"))
    db.upsert_friche(rec, origine)
    return rec


@pytest.fixture
def c():
    with TestClient(app) as client:
        yield client
    for o in (ORIG, "csv-test"):
        db.delete_origin(o)
    db.setting_set("maj_auto", maj.DEFAUT)


def test_reglages_valides_et_refus(c):
    r = c.put("/api/maj", json={"actif": True, "heure": "02:30", "departements": ["59", "2a", "1"]}).json()
    assert r["reglages"]["departements"] == ["01", "2A", "59"] and r["prochaine"]
    assert c.put("/api/maj", json={"heure": "25:00"}).status_code == 422
    assert c.put("/api/maj", json={"departements": ["999"]}).status_code == 422
    assert c.put("/api/maj", json={"mode": "pack", "pack_url": ""}).status_code == 422  # actif sans serveur
    assert c.put("/api/maj", json={"actif": False, "mode": "pack", "pack_url": "ftp://x"}).status_code == 422


def test_planificateur_une_fois_par_jour():
    r = {**maj.DEFAUT, "actif": True, "heure": "03:00"}
    nuit = datetime(2026, 11, 27, 3, 0, 30)
    assert not maj.doit_lancer(r, datetime(2026, 11, 27, 2, 59), None)
    assert maj.doit_lancer(r, nuit, "2026-11-26")
    assert not maj.doit_lancer(r, nuit, "2026-11-27")  # déjà fait aujourd'hui
    assert maj.doit_lancer(r, datetime(2026, 11, 27, 9, 0), "2026-11-26")  # serveur éteint à 3 h : rattrapage
    assert not maj.doit_lancer({**r, "actif": False}, nuit, None)


def test_pack_aller_retour_et_conflits(c):
    _real("cf-test-1")
    _real("cf-test-2", "59512")
    db.upsert_commune("59512", db.get_commune("59350"))
    exp = c.get("/api/packs/export", params={"departements": "59"})
    assert exp.status_code == 200 and exp.content[:2] == b"\x1f\x8b"
    pack = maj.lire_pack(exp.content)
    assert {f["id"] for f in pack["friches"]} >= {"cf-test-1", "cf-test-2"}
    assert {x["code"] for x in pack["communes"]} >= {"59512"}
    assert all(not f["origine"].startswith("demo") for f in pack["friches"])
    # nouvelle base : les deux friches arrivent ; une friche CSV de même identifiant n'est pas écrasée
    db.delete_origin(ORIG)
    _real("cf-test-2", "59512", origine="csv-test")
    sim = c.post("/api/packs/import", files={"file": ("p.json.gz", exp.content)}, data={"dry_run": "true"}).json()
    assert sim["friches_nouvelles"] == 1 and len(sim["friches_ignorees"]) == 1 and sim["simulation"]
    assert "cf-test-1" not in db.friche_origins()
    rep = c.post("/api/packs/import", files={"file": ("p.json.gz", exp.content)}).json()
    assert rep["friches_nouvelles"] == 1 and db.friche_origins()["cf-test-1"] == ORIG
    assert db.friche_origins()["cf-test-2"] == "csv-test"
    # fichiers refusés
    assert c.post("/api/packs/import", files={"file": ("x.json", b'{"a":1}')}).status_code == 422
    assert c.post("/api/packs/import", files={"file": ("x.csv", b"\xff\xfe")}).status_code == 422
    assert c.get("/api/packs/export", params={"departements": "abc"}).status_code == 400


def test_mise_a_jour_apis_journal_et_ecarts(c, monkeypatch):
    _real("cf-reste")
    _real("cf-disparue")
    _real("cf-baisse")
    monkeypatch.setattr(territoire, "communes_du_departement", lambda dep: ["59999"])
    seen = {}

    def fake_ingest(codes, georisques, dvf, progress, communes, sites, max_codes=50, refresh=False):
        seen.update(codes=codes, max_codes=max_codes, refresh=refresh)
        db.update_friche_values("cf-baisse", {"pollution_constatee": ("forte", Provenance("Diagnostic", "2026-11-26", "eleve")), "ppri": ("rouge", Provenance("PPR", "2026-11-26", "eleve"))})
        new = _real("cf-nouvelle")
        return {"importees": 3, "erreurs": [], "communes_interrogees": ["59350", "59999"],
                "ids_importes": ["cf-reste", "cf-baisse", new.get("id")]}
    monkeypatch.setattr(service, "ingest_cartofriches", fake_ingest)
    monkeypatch.setattr(service, "enrich_sites", lambda *a, **k: {"erreurs": []})  # friches CSV d'autres tests : pas de réseau
    monkeypatch.setattr(service, "enrich_communes", lambda *a, **k: {"erreurs": []})
    r = {**maj.DEFAUT, "departements": ["59"], "rafraichir_fichiers": True}
    res = maj.executer(None, "manuel", r)
    assert seen["codes"] == ["59350", "59999"] and seen["max_codes"] is None and seen["refresh"] is True
    assert [x["id"] for x in res["absentes_de_cartofriches"]] == ["cf-disparue"]  # signalée, pas supprimée
    assert "cf-disparue" in db.friche_origins()
    assert [x["id"] for x in res["nouvelles"]] == ["cf-nouvelle"]
    assert any(x["id"] == "cf-baisse" and (x["ecart"] is None or x["ecart"] < 0) for x in res["scores_modifies"])
    j = c.get("/api/maj").json()["journal"][0]
    assert j["etat"] == "termine" and j["declencheur"] == "manuel" and j["resume"]["nb_nouvelles"] == 1


def test_publication_et_mode_pack(c, monkeypatch):
    _real("cf-pub")
    pubs = maj.publier_packs()
    assert any(p["departement"] == "59" for p in pubs)
    served = c.get("/api/packs/59.json.gz")
    assert served.status_code == 200 and c.get("/api/packs/12.json.gz").status_code == 404
    assert any(p["departement"] == "59" for p in c.get("/api/packs").json()["packs"])
    raw = maj.lire_pack(served.content)
    assert any(f["id"] == "cf-pub" for f in raw["friches"])
    db.delete_origin(ORIG)

    def handler(req):
        if req.url.path == "/api/packs/59.json.gz":
            return httpx.Response(200, content=served.content)
        return httpx.Response(404)
    monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(handler))
    res = maj.executer(None, "nuit", {**maj.DEFAUT, "mode": "pack", "pack_url": "https://central.example", "departements": ["59", "62"]})
    assert res["packs"]["59"]["friches_nouvelles"] >= 1 and "cf-pub" in db.friche_origins()
    assert len(res["erreurs"]) == 1 and "62" in res["erreurs"][0]
    assert db.maj_list(1)[0]["etat"] == "termine_avec_erreurs"
