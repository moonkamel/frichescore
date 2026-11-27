"""Mode en ligne : connexion, rôles et isolation des données entre organismes."""
import os
import sqlite3
import tempfile
import threading

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "c.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, tenant  # noqa: E402
from app.main import app  # noqa: E402
from app.scoring import params  # noqa: E402

PW = "Motdepasse-Solide-2026"
CSV = ("site_id;site_nom;comm_insee;comm_nom;latitude;longitude;site_surface;site_type;basias_count\n"
       "PRIV1;Usine privée;59350;Lille;50.6;3.05;12000;friche industrielle;2\n")


def _user(email, org, role):
    if not db.user_by_email(email):
        db.user_create(email, None, org, role, tenant.hash_password(PW))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FRICHESCORE_AUTH", "1")
    monkeypatch.setenv("FRICHESCORE_COOKIE_SECURE", "0")
    auth._ECHECS.clear()
    with TestClient(app):
        pass  # initialisation de la base
    if db.is_empty():
        from app import service
        service.load_demo()  # base commune de test (en ligne, la démonstration n'est pas chargée d'office)
    for oid in ("org-a", "org-b"):
        if not db.org_get(oid):
            db.org_create(oid, oid.upper())
        db.org_update(oid, actif=1)
    _user("a@a.fr", "org-a", "analyste")
    _user("admin@a.fr", "org-a", "admin")
    _user("b@b.fr", "org-b", "analyste")
    _user("lect@b.fr", "org-b", "lecteur")
    _user("plateforme@x.fr", None, "admin_plateforme")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM friches WHERE org IN ('org-a', 'org-b')")
        for t in ("field_data", "portfolio", "parametres", "signaux"):
            c.execute(f"DELETE FROM {t} WHERE org IN ('org-a', 'org-b')")
        c.execute("DELETE FROM sessions")


def login(email):
    c = TestClient(app)
    r = c.post("/api/auth/connexion", json={"email": email, "mot_de_passe": PW})
    assert r.status_code == 200, r.text
    return c


def a_common_site(c):
    return next(x["id"] for x in c.post("/api/scores", json={"type": "departement", "values": ["59"]}).json()["sites"]
                if x["evaluable"] and str(x.get("origine")).startswith("demo"))


def test_connexion_obligatoire(env):
    c = TestClient(app)
    assert c.post("/api/scores", json={}).status_code == 401
    assert c.get("/api/auth/moi").status_code == 401
    assert c.get("/").status_code == 200  # l'interface se charge et affiche l'écran de connexion
    bad = {"email": "a@a.fr", "mot_de_passe": "faux"}
    assert c.post("/api/auth/connexion", json=bad).status_code == 401
    for _ in range(5):
        c.post("/api/auth/connexion", json=bad)
    assert c.post("/api/auth/connexion", json={"email": "a@a.fr", "mot_de_passe": PW}).status_code == 429
    auth._ECHECS.clear()
    a = login("a@a.fr")
    me = a.get("/api/auth/moi").json()["utilisateur"]
    assert me["organisme"]["id"] == "org-a" and me["role"] == "analyste"
    a.post("/api/auth/deconnexion")
    assert a.post("/api/scores", json={}).status_code == 401


def test_isolation_des_donnees(env):
    a, b = login("a@a.fr"), login("b@b.fr")
    r = a.post("/api/import/csv", files={"file": ("t.csv", CSV.encode(), "text/csv")}, data={"source": "Inventaire A"}).json()
    assert r["importees"] == 1
    ids_a = {x["id"] for x in a.post("/api/scores", json={"type": "communes", "values": ["59350"]}).json()["sites"]}
    ids_b = {x["id"] for x in b.post("/api/scores", json={"type": "communes", "values": ["59350"]}).json()["sites"]}
    assert "PRIV1" in ids_a and "PRIV1" not in ids_b and ids_b <= ids_a
    assert b.post("/api/sites/PRIV1", json={}).status_code == 404
    assert b.post("/api/sites/PRIV1/terrain", json={"key": "nb_proprietaires", "value": 1}).status_code == 404
    # B importe le même identifiant : pas d'écrasement, identifiant suffixé
    b.post("/api/import/csv", files={"file": ("t.csv", CSV.encode(), "text/csv")}, data={"source": "Inventaire B"})
    assert "PRIV1@org-b" in {x["id"] for x in b.post("/api/scores", json={"type": "communes", "values": ["59350"]}).json()["sites"]}
    assert a.post("/api/sites/PRIV1", json={}).json()["values"]["nom"] == "Usine privée"
    # données terrain et portfolio sur une friche commune : propres à chaque organisme
    sid = a_common_site(a)
    assert a.post(f"/api/sites/{sid}/terrain", json={"key": "nb_proprietaires", "value": 7, "source": "Relevé A"}).status_code == 200
    assert a.post(f"/api/sites/{sid}", json={}).json()["values"]["nb_proprietaires"] == 7
    assert b.post(f"/api/sites/{sid}", json={}).json()["values"].get("nb_proprietaires") != 7
    a.put(f"/api/portfolio/{sid}", json={"stage": "en_etude"})
    assert sid in [x["id"] for x in a.post("/api/portfolio/list", json={}).json()["items"]]
    assert sid not in [x["id"] for x in b.post("/api/portfolio/list", json={}).json()["items"]]
    # les données « origine » d'un organisme ne sont pas listées chez l'autre
    orig_b = [o["origine"] for o in b.get("/api/meta").json()["perimeters"]["origines"]]
    assert "inventaire-a" not in " ".join(orig_b).lower()


def test_parametres_par_organisme(env):
    adm_a, b = login("admin@a.fr"), login("b@b.fr")
    sid = a_common_site(b)
    g = lambda c: c.post(f"/api/sites/{sid}", json={}).json()["globals"]  # noqa: E731
    before_a, before_b = g(adm_a), g(b)
    r = adm_a.put("/api/parametres", json={"changes": [{"path": "CONFIDENCE_MAX_PENALTY", "value": 50}]})
    assert r.status_code == 200
    after_a, after_b = g(adm_a), g(b)
    u = next(iter(before_a))
    assert after_a[u]["penalite"] > before_a[u]["penalite"] and after_b[u]["penalite"] == before_b[u]["penalite"]
    assert b.put("/api/parametres", json={"changes": []}).status_code == 403  # analyste : pas les paramètres
    # calculs simultanés de deux organismes : chacun garde ses paramètres
    res = {"a": set(), "b": set()}

    def run(client, k):
        for _ in range(6):
            res[k].add(round(client.post(f"/api/sites/{sid}", json={}).json()["globals"][u]["penalite"], 3))
    ts = [threading.Thread(target=run, args=(adm_a, "a")), threading.Thread(target=run, args=(b, "b"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res["a"] == {round(after_a[u]["penalite"], 3)} and res["b"] == {round(before_b[u]["penalite"], 3)}
    adm_a.post("/api/parametres/reset")
    assert params.get_value("CONFIDENCE_MAX_PENALTY") in (20, 50)


def test_roles(env):
    lect, a, adm_a, pf = login("lect@b.fr"), login("a@a.fr"), login("admin@a.fr"), login("plateforme@x.fr")
    sid = a_common_site(lect)
    assert lect.post(f"/api/sites/{sid}", json={}).status_code == 200  # lecture
    assert lect.put(f"/api/portfolio/{sid}", json={"stage": "reperee"}).status_code == 403
    assert lect.post("/api/ia/question", json={"question": "et si ?"}).status_code == 403
    assert a.get("/api/comptes").status_code == 403
    assert a.get("/api/organismes").status_code == 403 and adm_a.get("/api/organismes").status_code == 403
    assert a.put("/api/maj", json={}).status_code == 403 and adm_a.get("/api/packs").status_code == 403
    # l'administrateur d'un organisme gère ses comptes, et seulement les siens
    comptes = adm_a.get("/api/comptes").json()["comptes"]
    assert {x["organisme_id"] for x in comptes} == {"org-a"} and all("mdp" not in x for x in comptes)
    assert adm_a.post("/api/comptes", json={"email": "n@a.fr", "mot_de_passe": "court", "role": "lecteur"}).status_code == 422
    assert adm_a.post("/api/comptes", json={"email": "n@a.fr", "mot_de_passe": PW, "role": "admin_plateforme"}).status_code == 403
    r = adm_a.post("/api/comptes", json={"email": "n@a.fr", "mot_de_passe": PW, "role": "lecteur", "organisme_id": "org-b"})
    assert r.status_code == 200 and db.user_by_email("n@a.fr")["organisme_id"] == "org-a"  # rattaché à son propre organisme
    b_user = db.user_by_email("b@b.fr")["id"]
    assert adm_a.patch(f"/api/comptes/{b_user}", json={"actif": False}).status_code == 404
    # plateforme : organismes
    assert pf.post("/api/organismes", json={"id": "Mauvais ID!", "nom": "x"}).status_code == 422
    assert pf.get("/api/organismes").status_code == 200
    # organisme suspendu : sessions refusées, connexion impossible
    pf.patch("/api/organismes/org-a", json={"actif": False})
    assert a.post("/api/scores", json={}).status_code == 401
    assert TestClient(app).post("/api/auth/connexion", json={"email": "a@a.fr", "mot_de_passe": PW}).status_code == 403
    pf.patch("/api/organismes/org-a", json={"actif": True})
    with db.conn() as c:
        c.execute("DELETE FROM utilisateurs WHERE email = 'n@a.fr'")


def test_signaux_et_taches_par_organisme(env):
    a, b = login("a@a.fr"), login("b@b.fr")
    sid = a_common_site(a)
    with tenant.as_org("org-a"):
        db.signal_add({"cle": "x", "friche_id": sid, "type": "bodacc_vente", "niveau": "opportunite", "titre": "privé A"})
    db.signal_add({"cle": "pub", "friche_id": sid, "type": "icpe_cessation", "niveau": "opportunite", "titre": "public", "org": None})
    ta = {s["titre"] for s in a.get("/api/signaux").json()["signaux"]}
    tb = {s["titre"] for s in b.get("/api/signaux").json()["signaux"]}
    assert {"privé A", "public"} <= ta and "privé A" not in tb and "public" in tb
    pub = next(s for s in a.get("/api/signaux").json()["signaux"] if s["titre"] == "public")
    a.post("/api/signaux/vu", json={"ids": [pub["id"]]})
    assert next(s for s in b.get("/api/signaux").json()["signaux"] if s["titre"] == "public")["vu"] == 0  # « lu » propre à A
    job = a.post("/api/enrichir/sites", json={"ids": ["inexistant"]}).json()
    assert any(j["id"] == job["id"] for j in a.get("/api/ingest/jobs").json()["taches"])
    assert not any(j["id"] == job["id"] for j in b.get("/api/ingest/jobs").json()["taches"])
    assert b.get(f"/api/ingest/jobs/{job['id']}").status_code == 404
    with db.conn() as c:
        c.execute("DELETE FROM signaux WHERE cle = 'pub'")


def test_migration_ancienne_base(monkeypatch, tmp_path):
    old = tmp_path / "ancienne.db"
    c = sqlite3.connect(old)
    c.executescript("""
        CREATE TABLE friches (id TEXT PRIMARY KEY, code_insee TEXT, departement TEXT, epci TEXT, origine TEXT, data TEXT NOT NULL, created_at TEXT);
        CREATE TABLE field_data (id INTEGER PRIMARY KEY AUTOINCREMENT, friche_id TEXT, key TEXT, value TEXT, source TEXT, date TEXT, confiance TEXT, auteur TEXT, created_at TEXT);
        CREATE TABLE portfolio (friche_id TEXT PRIMARY KEY, stage TEXT, usage TEXT, notes TEXT, updated_at TEXT);
        CREATE TABLE parametres (path TEXT PRIMARY KEY, value TEXT NOT NULL, note TEXT, updated_at TEXT);
        CREATE TABLE signaux (id INTEGER PRIMARY KEY AUTOINCREMENT, cle TEXT UNIQUE, friche_id TEXT, type TEXT, niveau TEXT, titre TEXT, detail TEXT, source TEXT, url TEXT, date_evenement TEXT, detecte_le TEXT, vu INTEGER DEFAULT 0);
        INSERT INTO portfolio VALUES ('S1', 'en_etude', NULL, 'note', '2026-01-01');
        INSERT INTO parametres VALUES ('CONFIDENCE_MAX_PENALTY', '25', NULL, '2026-01-01');
        INSERT INTO field_data (friche_id, key, value) VALUES ('S1', 'nb_proprietaires', '2');
    """)
    c.commit()
    c.close()
    monkeypatch.setattr(db, "DB_PATH", old)
    db.init()
    db.init()  # idempotent
    with tenant.as_org(tenant.LOCAL):
        assert db.portfolio()["S1"]["stage"] == "en_etude"
        assert db.param_overrides()["CONFIDENCE_MAX_PENALTY"]["value"] == 25
        assert db.list_field("S1")[0]["value"] == 2
