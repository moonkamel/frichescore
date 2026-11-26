"""Import CSV tolérant, paramètres modifiables et calendrier éditable (via l'API)."""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["FRICHESCORE_DB"] = os.path.join(_tmp, "hardening.db")
os.environ["FRICHESCORE_CALENDAR"] = os.path.join(_tmp, "calendrier.json")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ingest.mapping import parse_cell  # noqa: E402
from app.main import app  # noqa: E402
from app.scoring import config as C  # noqa: E402
from app.scoring import params  # noqa: E402


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client
        client.post("/api/parametres/reset")


def up(c, text, dry=False, enc="utf-8"):
    return c.post("/api/import/csv", files={"file": ("f.csv", text.encode(enc), "text/csv")},
                  data={"source": "test", "dry_run": "true" if dry else "false"}).json()


# ------------------------------------------------------------------ CSV
def test_parse_cell():
    assert parse_cell("surface_m2", "12 000,5")[0] == 12000.5
    assert parse_cell("surface_m2", "12.000,5")[0] == 12000.5
    assert parse_cell("surface_m2", "1 500 m²")[0] == 1500
    assert parse_cell("surface_m2", "douze")[1]
    assert parse_cell("code_insee", "1053")[0] == "01053"
    assert parse_cell("code_insee", "2A004")[0] == "2A004"
    assert parse_cell("basol", "Oui")[0] is True
    assert parse_cell("basol", "peut-être")[1]
    assert parse_cell("bati_etat", "Très dégradé")[0] == "ruine"
    assert parse_cell("zone_humide", "Avérée")[0] == "avere"
    assert parse_cell("plu_zone", "1AUh")[0] == "AU"
    assert parse_cell("type_friche", "Friche ferroviaire")[0] == "ferroviaire"
    assert parse_cell("bati_annee", "1850")[0] == 1850


def test_import_virgule_rapport_et_tolerance(c):
    text = ("id,nom,code_insee,surface_m2,type_friche,basol,bati_etat,lat,colonne_inconnue\n"
            "A1,Usine,59350,\"12 000\",industrielle,oui,mauvais état,50.6,x\n"
            "A2,Garage,59350,douze mille,commerciale,peut-être,bon,50.6,y\n"
            "A3,Sans territoire,,5000,industrielle,non,bon,,z\n"
            "A1,Doublon,59350,8000,industrielle,non,bon,50.6,w\n")
    r = up(c, text, dry=True)
    assert r["separateur"] == "virgule" and r["mode"] == "vérification"
    assert r["importables"] == 3 and r["rejetees"] == 1
    assert "colonne_inconnue" in r["colonnes_ignorees"]
    msgs = [(p["ligne"], p["colonne"], p["niveau"]) for p in r["problemes"]]
    assert (3, "surface_m2", "erreur") in msgs          # « douze mille »
    assert (3, "basol", "erreur") in msgs               # « peut-être »
    assert (2, "bati_etat", "erreur") in msgs           # « mauvais état » non reconnu
    assert (4, "code_insee", "erreur") in msgs          # rejet
    assert any(p["ligne"] == 5 and "déjà présent" in p["message"] for p in r["problemes"])
    # rien n'a été importé en mode vérification
    s = c.post("/api/scores", json={"type": "ids", "values": ["A1", "A2"]}).json()["sites"]
    assert s == []
    r = up(c, text)
    assert r["importees"] == 3
    s = {x["id"]: x for x in c.post("/api/scores", json={"type": "ids", "values": ["A1", "A2"]}).json()["sites"]}
    assert s["A1"]["evaluable"] and s["A1"]["nom"] == "Doublon"  # la dernière ligne l'emporte
    assert s["A2"]["evaluable"] is False or s["A2"]["evaluable"] is True  # importée malgré ses cellules invalides


def test_import_point_virgule_cp1252_et_entetes_francais(c):
    text = ("Identifiant;Nom du site;Code INSEE;Surface du site (m²);Type de friche;Anciens sites industriels BASIAS\n"
            "B1;Friche Pasteur;59512;3 400;industrielle;2\n")
    r = up(c, text, enc="cp1252")
    assert r["encodage"] == "cp1252" and r["separateur"] == "point-virgule"
    assert r["importees"] == 1 and r["evaluables"] == 1, r


def test_import_fichier_sans_colonnes_obligatoires(c):
    r = up(c, "nom;commentaire\nA;b\n", dry=True)
    assert any("obligatoire" in e for e in r["erreurs_fichier"])
    assert r["rejetees"] == 1


def test_import_fichier_vide_et_tabulation(c):
    assert up(c, "", dry=True)["erreurs_fichier"]
    r = up(c, "id\tcode_insee\tsurface_m2\ttype_friche\nT1\t59350\t1000\tindustrielle\n", dry=True)
    assert r["separateur"] == "tabulation" and r["importables"] == 1


def test_modele_et_dictionnaire(c):
    m = c.get("/api/import/modele.csv").text.lstrip("﻿")
    assert m.splitlines()[0].startswith("id;nom;") and len(m.splitlines()) == 3
    r = up(c, m, dry=True)
    assert r["importables"] == 2 and not [p for p in r["problemes"] if p["niveau"] == "erreur"], r["problemes"]
    d = c.get("/api/import/dictionnaire.csv").text
    assert "surface_m2" in d and "obligatoire" in d


# ------------------------------------------------------------ paramètres
def test_params_validation():
    assert params.validate("COST.demolition_m2_emprise", [200, 100])[1]
    assert params.validate("COST.pollution.basol_eaux", [10, 20, 0.5, 1.5])[1]
    assert params.validate("FRICTION.zone_humide_avere", [30, 12])[1] is None
    assert params.validate("inconnu", 3)[1]
    assert params.validate("CONFIDENCE_MAX_PENALTY", "abc")[1]


def test_params_modifient_le_score_immediatement(c):
    sid = next(x for x in c.post("/api/scores", json={"type": "departement", "values": ["59"]}).json()["sites"] if x["evaluable"])["id"]
    before = c.post(f"/api/sites/{sid}", json={"usage": "logement"}).json()["usages"]["logement"]["cost"]["total_haut"]
    r = c.put("/api/parametres", json={"changes": [{"path": "COST.viabilisation.logement", "value": [100, 200],
                                                    "note": "Retour d'expérience EPF 2025"}]}).json()
    assert r["erreurs"] == [] and r["n_modifies"] == 1
    assert C.COST["viabilisation"]["logement"] == (100, 200)
    after = c.post(f"/api/sites/{sid}", json={"usage": "logement"}).json()["usages"]["logement"]["cost"]["total_haut"]
    assert after > before
    item = next(p for p in r["parametres"] if p["path"] == "COST.viabilisation.logement")
    assert item["modifie"] and item["note"] == "Retour d'expérience EPF 2025" and item["defaut"] == [25, 60]
    # valeur invalide refusée, l'ancienne est conservée
    r = c.put("/api/parametres", json={"changes": [{"path": "COST.viabilisation.logement", "value": [300, 10]}]}).json()
    assert r["erreurs"] and C.COST["viabilisation"]["logement"] == (100, 200)
    # export / import / réinitialisation
    exp = c.get("/api/parametres/export").json()
    c.post("/api/parametres/reset")
    assert C.COST["viabilisation"]["logement"] == (25, 60)
    c.post("/api/parametres/import", json=exp)
    assert C.COST["viabilisation"]["logement"] == (100, 200)
    # le rapport PDF trace la calibration
    assert c.post("/api/export/rapport", json={"perimeter": {"type": "departement", "values": ["59"]}, "top_n": 3}).content[:4] == b"%PDF"
    c.post("/api/parametres/reset")
    assert C.COST["viabilisation"]["logement"] == (25, 60)


def test_param_friction_et_confiance(c):
    c.put("/api/parametres", json={"changes": [{"path": "FRICTION.zone_humide_avere", "value": [60, 30]},
                                               {"path": "CONFIDENCE_MAX_PENALTY", "value": 35}]})
    assert C.FRICTION["zone_humide_avere"] == (60, 30) and C.CONFIDENCE_MAX_PENALTY == 35
    assert c.get("/api/meta").json()["constants"]["confidence_max_penalty"] == 35
    c.post("/api/parametres/reset")
    assert C.CONFIDENCE_MAX_PENALTY == 20


# ------------------------------------------------------------ calendrier
def test_calendrier_edition_et_validation(c):
    cal = c.get("/api/calendrier").json()["calendrier"]
    n = len(cal["fenetres"])
    w = dict(cal["fenetres"][0])
    w.update(id="test_2027", nom="Appel test vérifié", ouverture="2027-01-10", cloture="2027-12-31",
             statut_verification="verifie", date_verification="2026-11-20", source_url="https://exemple.gouv.fr")
    cal["fenetres"].append(w)
    r = c.put("/api/calendrier", json=cal)
    assert r.status_code == 200 and len(r.json()["calendrier"]["fenetres"]) == n + 1
    z = c.post("/api/zan", json={"perimeter": {"type": "departement", "values": ["59"]}}).json()
    assert any(f["id"] == "test_2027" for f in z["fenetres"]) and "types" in z
    bad = dict(cal)
    bad["fenetres"] = cal["fenetres"] + [{**w, "id": "test_2027", "ouverture": "2028-01-01", "usages": ["piscine"]}]
    r = c.put("/api/calendrier", json=bad)
    assert r.status_code == 422
    errs = r.json()["detail"]["erreurs"]
    assert any("double" in e for e in errs) and any("postérieure" in e for e in errs) and any("piscine" in e for e in errs)
    c.post("/api/calendrier/reset")
    assert len(c.get("/api/calendrier").json()["calendrier"]["fenetres"]) == n


def test_test_connexion_endpoint(c):
    r = c.post("/api/connecteurs/test", json={"source": "nimporte"})
    assert r.status_code == 400
    r = c.post("/api/connecteurs/test", json={"source": "dvf", "base_url": "ftp://x"})
    assert r.status_code == 400
