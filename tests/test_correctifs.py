"""Correctifs après essais réels : jeu BPE renommé, Overpass surchargé, calendrier par défaut, cache navigateur."""
import os
import tempfile

os.environ.setdefault("FRICHESCORE_DB", os.path.join(tempfile.mkdtemp(), "k.db"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ingest import connectors as K  # noqa: E402
from app.ingest import site as SI  # noqa: E402
from app.ingest import territoire as T  # noqa: E402
from app.main import app  # noqa: E402
from app.scoring import calendar as cal  # noqa: E402


def test_bpe_identifiant_de_secours(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path.endswith("/datasets/"):  # recherche par titre
            return httpx.Response(200, json={"data": [
                {"title": "Base permanente des équipements (copie régionale)", "organization": {"name": "Région Île-de-France"},
                 "resources": [{"url": "https://idf/bpe23.csv", "title": "bpe23-nettoye.csv", "format": "csv"}]},
                {"title": "Base permanente des équipements (BPE)",
                 "organization": {"name": "Institut National de la Statistique et des Etudes Economiques (Insee)"},
                 "resources": [
                    {"url": "https://x/bpe_commune.csv", "title": "BPE ensemble des communes.csv", "format": "csv"},
                    {"url": "https://x/bpe_xy.csv", "title": "BPE géolocalisée xy.csv", "format": "csv"}]}]})
        if "/datasets/" in req.url.path:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(500)
    monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setitem(T.DATASETS["bpe"], "slug", "base-permanente-des-equipements-2")
    r = T.resolve_resource("bpe")
    assert r["url"] == "https://x/bpe_commune.csv" and any(p.endswith("/datasets/") for p in seen)


def test_overpass_serveur_de_secours(monkeypatch):
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        if req.url.host == "overpass-api.de":
            return httpx.Response(502, text="Bad gateway")
        return httpx.Response(200, json={"elements": [{"type": "node", "id": 1, "lat": 50.6, "lon": 3.0, "tags": {"highway": "bus_stop"}}]})
    monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(handler))
    stops = SI.fetch_stops((50.59, 2.99, 50.61, 3.01))
    assert len(stops) == 1 and hosts[0] == "overpass-api.de" and len(hosts) == 2

    monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(504)))
    try:
        SI.fetch_stops((50.59, 2.99, 50.61, 3.01))
        raise AssertionError("erreur attendue")
    except K.ConnectorError as e:
        assert "surchargés" in e.message


def test_calendrier_modele_present_et_secours(monkeypatch, tmp_path):
    assert cal.DEFAULT_FILE.exists()  # le modèle est bien livré avec l'application
    monkeypatch.setenv("FRICHESCORE_CALENDAR", str(tmp_path / "absent" / "cal.json"))
    monkeypatch.setattr(cal, "DEFAULT_FILE", tmp_path / "manquant.json")
    d = cal.load()
    assert d["fenetres"] == [] and d["echeances_reglementaires"] == []


def test_page_sans_cache():
    with TestClient(app) as c:
        r = c.get("/")
        assert "app.js?v=" in r.text and "style.css?v=" in r.text and r.headers["cache-control"] == "no-cache"


def test_cache_refait_si_la_regle_change(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "cache_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(T, "resolve_resource", lambda n: calls.append(n) or {"url": "https://x/f.csv", "titre": "f.csv", "date": "2026"})
    monkeypatch.setattr(T, "_download", lambda url, path, params=None: path.write_text("a;b\n1;2\n"))
    T.cached_file("bpe")
    T.cached_file("bpe")
    assert calls == ["bpe"]  # cache réutilisé
    monkeypatch.setitem(T.DATASETS["bpe"], "organisation", "autre-regle")
    T.cached_file("bpe")
    assert calls == ["bpe", "bpe"]  # règle modifiée : nouvelle résolution
