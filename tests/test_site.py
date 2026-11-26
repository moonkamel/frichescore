"""Indicateurs de site : gares SNCF, transports collectifs OSM, PVGIS, risques Géorisques (réponses simulées)."""
import json

import httpx
import pytest

from app.ingest import connectors as K
from app.ingest import site as SI
from app.scoring.record import Provenance, Record

GARES_CSV = ("nom;libellecourt;segment_drg;position_geographique;codeinsee;voyageurs\n"
             "Lille Flandres;LFL;a;50.6365, 3.0707;59350;O\n"
             "Lille Europe;LEU;a;50.6392, 3.0756;59350;O\n"
             "Fret Lille Délivrance;FLD;;50.60, 3.10;59350;N\n")

OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 50.6170, "lon": 3.0750, "tags": {"highway": "bus_stop", "name": "Arrêt A"}},
    {"type": "node", "id": 2, "lat": 50.6170, "lon": 3.0750, "tags": {"public_transport": "platform"}},  # doublon superposé
    {"type": "node", "id": 3, "lat": 50.6180, "lon": 3.0760, "tags": {"highway": "bus_stop"}},
    {"type": "node", "id": 4, "lat": 50.6190, "lon": 3.0740, "tags": {"railway": "station", "station": "subway", "name": "Métro M"}},
    {"type": "node", "id": 5, "lat": 50.6365, "lon": 3.0707, "tags": {"railway": "station", "name": "Lille Flandres"}},
]}

PVGIS = {"inputs": {}, "outputs": {"totals": {"fixed": {"E_y": 1010.5, "H(i)_y": 1265.3, "SD_y": 40}}}, "meta": {}}

RAPPORT = {"adresse": {"libelle": "x"}, "risquesNaturels": {
    "inondation": {"present": True, "libelle": "Inondation", "libelleStatutCommune": "Risque existant", "libelleStatutAdresse": "Risque existant"},
    "retraitGonflementArgile": {"present": True, "libelle": "Retrait-gonflement des sols argileux", "libelleStatutAdresse": "Exposition forte"},
    "seismicite": {"present": True, "libelle": "Séisme", "libelleStatutAdresse": "Zone 2"}}}


@pytest.fixture
def mock(monkeypatch, tmp_path):
    monkeypatch.setattr(SI, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(SI.time, "sleep", lambda s: None)

    def install(handler):
        monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(handler))
    return install


def handler(req):
    u = str(req.url)
    if "ressources.data.sncf.com" in u:
        return httpx.Response(200, text=GARES_CSV)
    if "overpass" in u:
        return httpx.Response(200, json=OVERPASS)
    if "PVcalc" in u:
        return httpx.Response(200, json=PVGIS)
    if "resultats_rapport_risque" in u:
        return httpx.Response(200, json=RAPPORT)
    if "gaspar/risques" in u:
        return httpx.Response(200, json={"data": [{"risques_detail": [{"libelle_risque_long": "Inondation"}]}]})
    return httpx.Response(404)


def site_rec(sid="CF-1", lat=50.6166, lon=3.0745):
    r = Record()
    p = Provenance("t", "2026", "moyen")
    for k, v in {"id": sid, "lat": lat, "lon": lon, "code_insee": "59350"}.items():
        r.set(k, v, p)
    return r


def test_gares_sncf_point_geographique_et_voyageurs(mock):
    mock(handler)
    pts, meta = SI.load_stations()
    assert len(pts) == 2 and meta["dataset"] == "gares-de-voyageurs"  # la gare fret est exclue
    g, d = SI.nearest(pts, 50.6166, 3.0745)
    assert g["nom"] == "Lille Flandres" and 2.0 < d < 2.4


def test_desserte_osm():
    st = [{"lat": 50.6170, "lon": 3.0750, "lourd": False, "train": False}, {"lat": 50.6180, "lon": 3.0760, "lourd": False, "train": False},
          {"lat": 50.6190, "lon": 3.0740, "lourd": True, "train": False}]
    assert SI.desserte(st, 50.6166, 3.0745)["classe"] == "forte"  # métro à ~270 m
    assert SI.desserte(st[:2], 50.6166, 3.0745)["classe"] == "moyenne"
    assert SI.desserte([], 50.6166, 3.0745)["classe"] == "faible"


def test_fetch_stops_dedoublonne(mock):
    mock(handler)
    stops = SI.fetch_stops((50.6, 3.0, 50.7, 3.1))
    assert len(stops) == 4 and sum(s["lourd"] for s in stops) == 2


def test_pvgis_et_risques(mock):
    mock(handler)
    h, d = SI.fetch_pvgis(50.62, 3.07)
    assert h == 1265.3 and d["E_y"] == 1010.5
    v, d = SI.fetch_risques_adresse(50.62, 3.07)
    assert v == {"risque_inondation": True, "alea_argiles": "fort"}


def test_risques_repli_commune(mock):
    mock(lambda req: httpx.Response(503) if "rapport" in str(req.url) else handler(req))
    out, rep = SI.enrich_sites([site_rec()], adresse=True)
    v = out["CF-1"]
    assert v["risque_inondation"][0] is True and v["risque_inondation"][1].confiance == "moyen"
    assert "GASPAR" in v["risque_inondation"][1].source
    assert v["desserte_tc"][0] == "forte" and v["distance_gare_km"][1].source.startswith("Gares")
    assert v["irradiation_kwh_m2"][0] == 1265
    assert any("rapport de risques" in e for e in rep["erreurs"])


def test_gares_en_echec_repli_osm(mock):
    mock(lambda req: httpx.Response(500) if "sncf" in str(req.url) else handler(req))
    out, rep = SI.enrich_sites([site_rec()], adresse=False)
    assert "OpenStreetMap" in out["CF-1"]["distance_gare_km"][1].source


def test_scoring_utilise_les_risques():
    from app.scoring.engine import evaluate_usage, recommendations
    from tests.test_scoring import site
    base = evaluate_usage(site(), "logement")
    r = site()
    del r.values["ppri"]
    r.set("risque_inondation", True, Provenance("Géorisques", "2026", "eleve"))
    r.set("alea_argiles", "fort", Provenance("Géorisques", "2026", "eleve"))
    ur = evaluate_usage(r, "logement")
    assert ur["dims"]["d3"] > base["dims"]["d3"] and ur["cost"]["total_haut"] > base["cost"]["total_haut"]
    assert any("PPRi" in x["etude"] for x in recommendations(r))


# ------------------------------------------------ urbanisme, nature, milieux humides
def test_vocation_plu():
    assert SI.plu_from_props({"typezone": "U", "destdomi": "02", "libelle": "UE"})["plu_vocation"] == "activite"
    p = SI.plu_from_props({"typezone": "AUc", "destdomi": "00", "libelong": "Zone à urbaniser à vocation d'habitat"})
    assert p["plu_zone"] == "AU" and p["plu_vocation"] == "habitat"
    assert SI.plu_from_props({"typezone": "U", "libelle": "UB"})["plu_vocation"] == "mixte"
    assert SI.plu_from_props({"typezone": "N", "libelle": "Nh"}) ["plu_zone"] == "N"


def gpu_handler(req):
    u = str(req.url)
    if "gpu/zone-urba" in u:
        assert json.loads(req.url.params["geom"])["type"] == "Point"
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [
            {"properties": {"libelle": "UE", "libelong": "Zone urbaine d'activités économiques", "typezone": "U", "destdomi": "02"}}]})
    if "assiette-sup-s" in u:
        return httpx.Response(200, json={"features": [{"properties": {"suptype": "ac1"}}, {"properties": {"suptype": "pm1"}}]})
    if "natura-habitat" in u or "znieff2" in u:
        return httpx.Response(200, json={"features": [{"properties": {}}]} if "znieff2" in u else {"features": []})
    if "nature/" in u:
        return httpx.Response(200, json={"features": []})
    if "GetCapabilities" in u:
        return httpx.Response(200, text="<wfs:FeatureTypeList><FeatureType><Name>AUTRE:couche</Name></FeatureType>"
                                        "<FeatureType><Name>MILIEUX_HUMIDES:probabilite_drom</Name></FeatureType>"
                                        "<FeatureType><Name>MILIEUX_HUMIDES:milieux_potentiellement_humides_seuil</Name></FeatureType>"
                                        "</wfs:FeatureTypeList>")
    if "GetFeature" in u:
        assert req.url.params["TYPENAMES"] == "MILIEUX_HUMIDES:milieux_potentiellement_humides_seuil"
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [{"properties": {}}]})
    return handler(req)


def test_urbanisme_nature_humides(mock):
    mock(gpu_handler)
    v, d = SI.fetch_gpu(50.62, 3.07)
    assert v == {"plu_zone": "U", "plu_vocation": "activite", "servitudes_count": 2} and d["libelle"] == "UE"
    v, _ = SI.fetch_nature(50.62, 3.07)
    assert v == {"natura2000": False, "znieff": True}
    v, d = SI.fetch_zones_humides(50.62, 3.07)
    assert v == {"zone_humide": "probable"} and "seuil" in d["couche"]


def test_gpu_sans_document_d_urbanisme(mock):
    mock(lambda req: httpx.Response(200, json={"features": []}))
    with pytest.raises(K.ConnectorError, match="RNU"):
        SI.fetch_gpu(50.62, 3.07)


def test_enrichissement_complet_avec_urbanisme(mock):
    mock(gpu_handler)
    out, rep = SI.enrich_sites([site_rec()], adresse=False)
    v = out["CF-1"]
    assert v["plu_vocation"][0] == "activite" and v["plu_vocation"][1].confiance == "moyen"
    assert v["zone_humide"][0] == "probable" and "prélocalisation" in v["zone_humide"][1].methode
    assert v["znieff"][0] is True and v["servitudes_count"][0] == 2
