"""Signaux d'opportunité : changements entre mises à jour, annonces BODACC (simulées), affichage."""
import json
import os
import tempfile

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "s.db"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, signaux as SG  # noqa: E402
from app.ingest import connectors as K  # noqa: E402
from app.main import app  # noqa: E402
from app.scoring.record import Provenance  # noqa: E402

P = Provenance("test", "2026-01-01", "eleve")
NOM_PERSONNE = "DUPONT Jean"

ANNONCES = {"total_count": 4, "results": [
    {"id": "A202601", "dateparution": "2026-09-14", "familleavis": "collective", "familleavis_lib": "Procédures collectives",
     "registre": ["552 100 554", "552100554"], "commercant": NOM_PERSONNE, "tribunal": "Tribunal de commerce de Lille Métropole",
     "jugement": json.dumps({"famille": "Jugement d'ouverture", "nature": "Jugement d'ouverture de liquidation judiciaire"})},
    {"id": "A202602", "dateparution": "2026-05-02", "familleavis": "modification", "familleavis_lib": "Modifications diverses",
     "registre": ["552100554"], "commercant": NOM_PERSONNE},
    {"id": "A202603", "dateparution": "2026-03-01", "familleavis": "vente", "familleavis_lib": "Ventes et cessions",
     "registre": ["552100554"], "commercant": NOM_PERSONNE},
    {"id": "A202604", "dateparution": "2026-02-01", "familleavis": "collective", "familleavis_lib": "Procédures collectives",
     "registre": ["999888777"], "commercant": "AUTRE"},
]}


def test_siren_et_icpe():
    assert K.siren_of("552 100 554 00012") == "552100554" and K.siren_of("552100554") == "552100554"
    assert K.siren_of("12345") is None and K.siren_of(None) is None
    f = K._icpe_fields([{"regime": "Autorisation", "etatActivite": "En fonctionnement", "siret": "55210055400012",
                         "raisonSociale": NOM_PERSONNE}], "2026-11-26")
    assert f["siren_exploitants"] == ["552100554"] and NOM_PERSONNE not in json.dumps(f)


def test_changements_entre_mises_a_jour():
    avant = {"s1": {"icpe_statut": "en_activite", "plu_zone": "N"}, "s2": {"projet_connu": False, "plu_zone": "U"}}
    apres = {"s1": {"icpe_statut": "cessee", "plu_zone": "U"}, "s2": {"projet_connu": True, "plu_zone": "A"},
             "s3": {"icpe_statut": "cessee"}}  # nouvelle friche : pas de « changement »
    sig = {(s["friche_id"], s["type"]): s for s in SG.changements(avant, apres, "2026-11-27")}
    assert sig[("s1", "icpe_cessation")]["niveau"] == "opportunite"
    assert sig[("s1", "plu_modifie")]["niveau"] == "opportunite" and "N → U" in sig[("s1", "plu_modifie")]["titre"]
    assert sig[("s2", "projet_apparu")]["niveau"] == "alerte" and sig[("s2", "plu_modifie")]["niveau"] == "alerte"
    assert not any(k[0] == "s3" for k in sig)


@pytest.fixture
def c(monkeypatch):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=ANNONCES)
    monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(handler))
    with TestClient(app) as client:
        sid = next(k for k, r in db.load_sites({"type": "departement", "values": ["59"]}).items())
        rec = db.load_sites({"type": "ids", "values": [sid]})[sid]
        rec.values.pop("_origine", None)
        rec.set("id", "sig-1", P)
        rec.set("siren_exploitants", ["55210055400012"], P)
        db.upsert_friche(rec, "csv-signaux")
        client.seen = seen
        yield client
    db.delete_origin("csv-signaux")
    with db.conn() as cx:
        cx.execute("DELETE FROM signaux")


def test_bodacc_signaux_sans_nom_et_dedoublonnes(c):
    rep = SG.scan_bodacc(db.load_sites({"type": "tout"}))
    assert rep["sirens"] == 1 and rep["nouveaux"] == 2  # liquidation + vente ; modification et autre SIREN ignorées
    assert "552100554" in c.seen[0] and "552+100+554" in c.seen[0].replace("%20", "+")
    rows = db.signals_list("sig-1")
    assert {r["type"] for r in rows} == {"bodacc_procedure_collective", "bodacc_vente"}
    liq = next(r for r in rows if r["type"] == "bodacc_procedure_collective")
    assert "liquidation judiciaire" in liq["detail"] and "l'exploitant ICPE" in liq["titre"] and liq["url"]
    assert NOM_PERSONNE not in json.dumps(rows)
    assert SG.scan_bodacc(db.load_sites({"type": "tout"}))["nouveaux"] == 0  # déjà connus


def test_api_signaux(c):
    c.post("/api/signaux/rechercher")
    import time
    for _ in range(50):
        if db.signals_list("sig-1"):
            break
        time.sleep(0.1)
    v = c.get("/api/signaux").json()
    assert v["non_vus"] >= 2 and v["opportunites_non_vues"] >= 2
    s = next(x for x in v["signaux"] if x["friche_id"] == "sig-1")
    assert s["nom"] and not s["supprimee"]
    site = next(x for x in c.post("/api/scores", json={"type": "ids", "values": ["sig-1"]}).json()["sites"])
    assert site["signaux"]["opportunites"] >= 2
    assert c.post("/api/sites/sig-1", json={}).json()["signaux"]
    v2 = c.post("/api/signaux/vu", json={"ids": [s["id"]]}).json()
    assert v2["non_vus"] == v["non_vus"] - 1
    assert c.post("/api/signaux/vu", json={"ids": None}).json()["non_vus"] == 0
    t = c.post("/api/connecteurs/test", json={"source": "bodacc", "siren": "552100554"}).json()
    assert t["ok"] and len(t["annonces"]) == 2
    assert not c.post("/api/connecteurs/test", json={"source": "bodacc", "siren": "12"}).json()["ok"]
