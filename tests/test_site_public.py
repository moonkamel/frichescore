"""Page de présentation publique, référencement et demandes de démonstration."""
import os
import tempfile

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "p.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, main  # noqa: E402
from app.main import app  # noqa: E402


def test_local_vs_en_ligne(monkeypatch):
    with TestClient(app) as c:
        assert 'id="tabs"' in c.get("/").text  # local : l'application
        p = c.get("/presentation")
        assert p.status_code == 200 and "<h1>" in p.text and "{{SITE_URL}}" not in p.text
        monkeypatch.setenv("FRICHESCORE_AUTH", "1")
        monkeypatch.setenv("FRICHESCORE_SITE_URL", "https://exemple.test")
        home = c.get("/").text
        assert "Quelles friches industrielles" in home and 'rel="canonical" href="https://exemple.test/"' in home
        assert 'id="tabs"' in c.get("/app").text  # l'application, accessible sans session (écran de connexion)
        assert c.get("/site/assets/ecran-classement.webp").status_code == 200
        assert "Sitemap: https://exemple.test/sitemap.xml" in c.get("/robots.txt").text
        assert "<loc>https://exemple.test/</loc>" in c.get("/sitemap.xml").text
        assert c.get("/mentions-legales").status_code == 200


def test_formulaire_de_demo(monkeypatch):
    monkeypatch.setenv("FRICHESCORE_AUTH", "1")
    main._CONTACTS.clear()
    with TestClient(app) as c:
        ok = {"nom": "Camille", "email": "c@epf.fr", "organisme": "EPF", "profil": "Aménageur", "territoire": "59"}
        assert c.post("/api/contact", json=ok).status_code == 200  # ouvert sans connexion
        assert db.demande_list()[0]["email"] == "c@epf.fr"
        assert c.post("/api/contact", json={**ok, "email": "pas-une-adresse"}).status_code == 422
        n = len(db.demande_list())
        assert c.post("/api/contact", json={**ok, "site_web": "spam"}).status_code == 200 and len(db.demande_list()) == n
        for _ in range(5):
            c.post("/api/contact", json=ok)
        assert c.post("/api/contact", json=ok).status_code == 429
        assert c.get("/api/demandes").status_code == 401  # réservé à la plateforme
