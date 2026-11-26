"""Connecteurs : réponses simulées (httpx.MockTransport) calquées sur les formats
publics de Cartofriches, Géorisques v1 et geo-dvf. On vérifie le parsing, la
pagination et surtout que les erreurs réseau ou de format ne font jamais planter
l'outil ni n'inventent de données."""
import json
from pathlib import Path

import httpx
import pytest

from app.ingest import connectors as K
from app.scoring.record import Record

FX = Path(__file__).parent / "fixtures"


def fx(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


@pytest.fixture
def mock(monkeypatch):
    """Installe un transport simulé : handler(request) -> httpx.Response."""
    def install(handler):
        monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(handler))
        monkeypatch.setattr(K, "CARTOFRICHES_URL", "https://apidf.test/cartofriches/friches/")
        monkeypatch.setattr(K, "GEORISQUES_URL", "https://georisques.test/api/v1")
        monkeypatch.setattr(K, "GEODVF_URL", "https://dvf.test/csv")
        monkeypatch.setattr(K.time, "sleep", lambda s: None)
    return install


# ------------------------------------------------------------- Cartofriches
def test_cartofriches_pagination_et_parsing(mock):
    def h(req):
        if "page=2" in str(req.url):
            return httpx.Response(200, json=fx("cartofriches_page2.json"))
        assert req.url.params["code_insee"] == "59512"
        return httpx.Response(200, json=fx("cartofriches_page1.json"))
    mock(h)
    recs, warns = K.fetch_cartofriches("59512")
    assert [r.get("id") for r in recs] == ["CF-59512_0001", "CF-59512_0002", "CF-59512_0003"]
    a = recs[0]
    assert a.get("type_friche") == "industrielle"
    assert a.get("bati_etat") == "degrade"
    assert a.get("plu_zone") == "U"
    assert a.get("basias_count") == 2 and a.prov["basias_count"].confiance == "faible"
    assert not a.has("basol")  # champ vide : « non renseigné », jamais « pas de BASOL »
    assert a.get("icpe_count") == 1
    assert a.get("statut_cartofriches") == "verifiee"
    assert recs[1].get("surface_m2") == 2300  # « 2 300 » toléré
    assert recs[1].get("statut_cartofriches") == "non_verifiee"


def test_cartofriches_geojson_polygone():
    rows = K.parse_cartofriches({"type": "FeatureCollection", "features": [
        {"properties": {"site_id": "1"}, "geometry": {"type": "Polygon", "coordinates": [[[3.0, 50.0], [3.2, 50.0], [3.2, 50.2], [3.0, 50.2]]]}}]})
    assert abs(rows[0]["longitude"] - 3.1) < 1e-9 and abs(rows[0]["latitude"] - 50.1) < 1e-9


def test_cartofriches_format_inattendu():
    with pytest.raises(K.ConnectorError):
        K.parse_cartofriches({"erreur": "maintenance"})


@pytest.mark.parametrize("status,frag", [(404, "introuvable"), (403, "refusé"), (429, "trop de requêtes"), (503, "HTTP 503")])
def test_cartofriches_erreurs_http(mock, status, frag):
    mock(lambda req: httpx.Response(status, text="<html>erreur</html>"))
    with pytest.raises(K.ConnectorError) as e:
        K.fetch_cartofriches("59512")
    assert frag in e.value.message


def test_cartofriches_json_invalide(mock):
    mock(lambda req: httpx.Response(200, text="<html>page de maintenance</html>"))
    with pytest.raises(K.ConnectorError) as e:
        K.fetch_cartofriches("59512")
    assert "JSON" in e.value.message


def test_cartofriches_timeout_et_connexion(mock):
    def timeout(req):
        raise httpx.ReadTimeout("lent", request=req)
    mock(timeout)
    with pytest.raises(K.ConnectorError) as e:
        K.fetch_cartofriches("59512")
    assert "délai dépassé" in e.value.message

    def refused(req):
        raise httpx.ConnectError("refused", request=req)
    mock(refused)
    with pytest.raises(K.ConnectorError) as e:
        K.fetch_cartofriches("59512")
    assert "connexion impossible" in e.value.message


# --------------------------------------------------------------- Géorisques
def _site():
    r = Record()
    r.set("id", "X")
    r.set("lat", 50.692)
    r.set("lon", 3.171)
    return r


def test_georisques_enrichissement_complet(mock):
    def h(req):
        assert req.url.params["latlon"] == "3.171,50.692"
        p = req.url.path
        if p.endswith("casias"):
            return httpx.Response(200, json=fx("georisques_casias.json"))
        if p.endswith("installations_classees"):
            return httpx.Response(200, json=fx("georisques_icpe.json"))
        return httpx.Response(200, json=fx("georisques_vide.json"))
    mock(h)
    rec, errs = K.enrich_georisques(_site())
    assert errs == []
    assert rec.get("basias_count") == 2 and rec.get("basol") is False and rec.get("sis") is False
    assert rec.get("icpe_count") == 1 and rec.get("icpe_regime") == "autorisation"
    assert rec.get("seveso") == "bas" and rec.get("icpe_statut") == "cessee"
    assert rec.prov["basias_count"].confiance == "faible" and rec.prov["basol"].confiance == "eleve"


def test_georisques_echec_partiel_ne_cree_pas_de_fausse_donnee(mock):
    def h(req):
        if req.url.path.endswith("conclusions_sis"):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=fx("georisques_vide.json"))
    mock(h)
    rec, errs = K.enrich_georisques(_site())
    assert len(errs) == 1 and "sis" in errs[0]
    assert not rec.has("sis")  # absente = non interrogée, jamais False par défaut
    assert rec.get("basol") is False and rec.get("icpe_count") == 0


def test_georisques_sans_coordonnees():
    r = Record()
    r.set("id", "X")
    rec, errs = K.enrich_georisques(r)
    assert errs and not rec.has("basol")


# --------------------------------------------------------------------- DVF
DVF = ("id_mutation,date_mutation,nature_mutation,valeur_fonciere,code_commune,type_local,surface_reelle_bati\n"
       "A,2022-01-01,Vente,200000,59350,Appartement,50\n"
       "A,2022-01-01,Vente,200000,59350,Dépendance,\n"
       "B,2022-02-01,Vente,330000,59350,Maison,110\n"
       "C,2022-03-01,Echange,100000,59350,Maison,100\n")


def test_dvf_fetch_et_annee_manquante(mock):
    def h(req):
        if "/2022/" in req.url.path:
            assert req.url.path.endswith("/communes/59/59350.csv")
            return httpx.Response(200, text=DVF)
        return httpx.Response(404)
    mock(h)
    ind, errs = K.fetch_dvf("59350", [2021, 2022])
    assert ind["prix_m2_logement"] == 3500 and ind["nb_mutations_3ans"] == 2
    assert len(errs) == 1 and "2021" in errs[0]


def test_dvf_fichier_illisible():
    with pytest.raises(K.ConnectorError):
        K.dvf_indicators({2022: "pas,un,fichier,dvf\n1,2,3,4\n"})


# --------------------------------------------------------- test de connexion
def test_test_connexion_succes_et_echec(mock):
    mock(lambda req: httpx.Response(200, json=fx("cartofriches_page2.json")))
    r = K.test_connection("cartofriches", "59512")
    assert r["ok"] and r["http_status"] == 200 and r["resultat"]["friches_exploitables"] == 1 and r["duree_ms"] is not None
    mock(lambda req: httpx.Response(502, text="Bad gateway"))
    r = K.test_connection("georisques")
    assert not r["ok"] and "aucune des 4 bases" in r["erreur"]
    r = K.test_connection("inconnue")
    assert not r["ok"]


def test_erreur_proxy_explicite(mock):
    def proxy(req):
        raise httpx.ProxyError("403 Forbidden", request=req)
    mock(proxy)
    r = K.test_connection("dvf", "59350")
    assert not r["ok"] and "proxy" in r["erreur"]


def test_nouvelle_tentative_apres_delai(mock):
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("lent", request=req)
        assert req.url.params["page_size"] == "500"
        return httpx.Response(200, json=fx("cartofriches_page2.json"))
    mock(h)
    recs, _ = K.fetch_cartofriches("59512")
    assert calls["n"] == 3 and len(recs) == 1  # 1 délai dépassé + /friches + /geofriches, fusionnés sans doublon


def test_adresse_cartofriches_alignee_sur_client_officiel():
    assert K.cartofriches_urls("https://x/cartofriches/geofriches") == ("https://x/cartofriches/friches", "https://x/cartofriches/geofriches")
    assert K.cartofriches_urls("https://x/cartofriches/") == ("https://x/cartofriches/friches", "https://x/cartofriches/geofriches")


def test_diagnostic_reseau_dns_inconnu():
    d = K.network_diagnostic("https://hote-inexistant.invalid/api")
    assert d["hote"] == "hote-inexistant.invalid" and "DNS" in d["conclusion"]


def test_transport_ipv4_par_defaut(monkeypatch):
    monkeypatch.setattr(K, "TRANSPORT", None)
    monkeypatch.setattr(K, "FORCE_IPV4", True)
    assert isinstance(K._transport(), httpx.HTTPTransport)
    monkeypatch.setattr(K, "FORCE_IPV4", False)
    assert isinstance(K._transport(), httpx.HTTPTransport)


def test_proxy_env_masque_identifiants(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.local:8080")
    assert "secret" not in str(K.proxy_env())


def test_contexte_tls_compact_reduit_le_clienthello():
    """Le mode compact doit produire un ClientHello nettement plus petit (pas d'échange de clés post-quantique)."""
    import ssl as _ssl

    def hello_size(ctx):
        inc, out = _ssl.MemoryBIO(), _ssl.MemoryBIO()
        obj = ctx.wrap_bio(inc, out, server_hostname="exemple.fr")
        try:
            obj.do_handshake()
        except _ssl.SSLWantReadError:
            pass
        return len(out.read())
    std, cmp_ = hello_size(K.ssl_context(compact=False)), hello_size(K.ssl_context(compact=True))
    assert cmp_ <= std and cmp_ < 1200
    assert K.ssl_context().verify_mode == _ssl.CERT_REQUIRED  # les certificats restent vérifiés


def test_transport_reel_utilise_contexte_tls(monkeypatch):
    monkeypatch.setattr(K, "TRANSPORT", None)
    assert isinstance(K._transport(), httpx.HTTPTransport)


def test_surface_du_site_prioritaire_et_inventaire_des_champs():
    rows = [{"site_id": "1", "unite_fonciere_surface": 9999, "site_surface": 1200, "site_type": "Friche X", "champ_inconnu": "a"},
            {"site_id": "2", "site_surface": 800, "site_type": "Friche X", "champ_inconnu": "a"}]
    recs, _ = K.cartofriches_records(rows)
    assert recs[0].get("surface_m2") == 1200
    rep = K.field_report(rows)
    assert "champ_inconnu" in rep["colonnes_non_reconnues"] and rep["valeurs_distinctes"]["site_type"] == ["Friche X"]
    assert rep["taux_remplissage"]["unite_fonciere_surface"] == "1/2"


GEO_REEL = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "id": "59350_10190", "geometry": {"type": "MultiPolygon", "coordinates": [[[[3.07, 50.61], [3.08, 50.61], [3.08, 50.62]]]]},
     "properties": {"site_nom": "Teinturerie", "site_type": "inconnu", "site_adresse": None, "site_statut": "friche avec projet",
                    "comm_nom": "LILLE", "comm_insee": "59350", "dep": "59", "proprio_personne": "personne physique, personne morale",
                    "unite_fonciere_surface": 234.17, "unite_fonciere_refcad": ["59350000IK0025", "59350000IK0026"],
                    "source_nom": "Site Basias ou Basol non vérifié par le Cerema", "nature": "MTE non vérifié",
                    "urba_zone_type": "U", "longitude": 3.0745, "latitude": 50.6166}}]}


def test_champs_reels_geofriches():
    """Format observé sur l'API réelle (novembre 2026) : identifiant porté par l'objet GeoJSON."""
    recs, warns = K.cartofriches_records(K.parse_cartofriches(GEO_REEL))
    assert not warns
    r = recs[0]
    assert r.get("id") == "CF-59350_10190" and r.get("lat") == 50.6166 and r.get("lon") == 3.0745
    assert r.get("surface_m2") == 234.17 and r.get("nb_parcelles") == 2
    assert r.get("proprietaire_type") == "mixte" and r.get("projet_connu") is True
    assert r.get("statut_cartofriches") == "verifiee" or r.get("statut_cartofriches") == "non_verifiee"
    assert "BASIAS" in r.get("activite_passee") and r.prov["activite_passee"].confiance == "faible"
    from app.scoring.engine import check_evaluable
    assert check_evaluable(r) == []


def test_fusion_sans_doublon_quand_identifiants_differents():
    attr = [{"site_id": "A", "site_nom": "Usine", "comm_insee": "59350", "site_surface": 1000, "site_type": "friche industrielle"}]
    geo = [{"site_id": "999", "site_nom": "Usine", "comm_insee": "59350", "unite_fonciere_surface": 1000, "longitude": 3.0, "latitude": 50.0}]
    m = K.merge_cartofriches(attr, geo)
    assert len(m) == 1 and m[0]["latitude"] == 50.0 and m[0]["site_id"] == "A"
