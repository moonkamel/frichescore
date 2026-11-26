"""Indicateurs communaux : détection tolérante des colonnes sur des fichiers simulés,
résolution des ressources data.gouv.fr et API Géo via un transport simulé."""
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import httpx
import pytest

from app.ingest import connectors as K
from app.ingest import territoire as T


def write(tmp, name, text, enc="utf-8"):
    p = Path(tmp) / name
    p.write_bytes(text.encode(enc))
    return p


def test_zonage_abc(tmp_path):
    p = write(tmp_path, "z.csv", "CODGEO;LIBGEO;Zonage ABC en vigueur\n59350;Lille;A\n59512;Roubaix;B1\n01001;L'Abergement;C\n2A004;Ajaccio;A bis\n")
    out, diag = T.parse_zonage(p, {"59350", "59512", "2A004", "99999"})
    assert out == {"59350": "A", "59512": "B1", "2A004": "Abis"} and diag["colonne_zone"] == "Zonage ABC en vigueur"


def test_zonage_colonnes_introuvables(tmp_path):
    p = write(tmp_path, "z.csv", "nom;valeur\nLille;12\n")
    with pytest.raises(K.ConnectorError):
        T.parse_zonage(p, {"59350"})


def test_lovac_taux_et_rapport(tmp_path):
    p = write(tmp_path, "l.csv", "CODGEO_25;LIBGEO_25;pp_vacant_24;pp_vacant_plus_2ans_24;pp_total_24;pp_vacant_25;pp_vacant_plus_2ans_25;pp_total_25\n"
                                 "59350;Lille;9000;3000;120000;9600;3100;120000\n59512;Roubaix;s;s;40000;5200;2000;40000\n", enc="cp1252")
    out, diag = T.parse_lovac(p, {"59350", "59512"})
    assert diag["colonne_vacants"] == "pp_vacant_25" and diag["colonne_total"] == "pp_total_25"
    assert out == {"59350": 8.0, "59512": 13.0}


def test_sitadel_moyenne_annuelle(tmp_path, monkeypatch):
    y = T.date.today().year - 1
    lines = ["REG;DEP;COMM;DATE_PRISE_EN_COMPTE;LOG_AUT;LOG_COM"]
    for yy in (y - 3, y - 2, y - 1, y):
        for m in (1, 6):
            lines.append(f"32;59;59350;{yy}-{m:02d};{100 if yy >= y - 2 else 999};50")
    p = write(tmp_path, "s.csv", "\n".join(lines) + "\n")
    out, diag = T.parse_sitadel(p, {"59350"})
    assert diag["colonne_logements"] == "LOG_AUT" and out["59350"] == 200  # 2 × 100 par an sur les 3 dernières années
    # même fichier compressé en ZIP
    zp = tmp_path / "s.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(p, "sitadel_communes.csv")
    out2, _ = T.parse_sitadel(zp, {"59350"})
    assert out2 == out


@pytest.fixture
def mock(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "cache_dir", lambda: tmp_path)

    def install(handler):
        monkeypatch.setattr(K, "TRANSPORT", httpx.MockTransport(handler))
    return install


def test_resolution_ressource_et_cache(mock, tmp_path):
    calls = {"dl": 0}

    def h(req):
        if "/api/1/datasets/" in str(req.url):
            return httpx.Response(200, json={"resources": [
                {"title": "Liste des 468 communes reclassées - Zonage ABC 5 septembre 2025.csv", "url": "https://f/reclass.csv", "format": "csv"},
                {"title": "Liste ensemble des communes - Zonage ABC en vigueur 26 juin 2026.csv", "url": "https://f/all.csv", "format": "csv",
                 "last_modified": "2026-07-03T10:00:00"},
                {"title": "Documentation.pdf", "url": "https://f/doc.pdf", "format": "pdf"}]})
        calls["dl"] += 1
        assert str(req.url) == "https://f/all.csv"
        return httpx.Response(200, text="CODGEO;ZONE\n59350;A\n")
    mock(h)
    path, meta = T.cached_file("zonage_abc")
    assert meta["titre"].startswith("Liste ensemble") and path.exists()
    T.cached_file("zonage_abc")
    assert calls["dl"] == 1  # deuxième appel servi par le cache


def test_api_geo(mock):
    mock(lambda req: httpx.Response(200, json={"nom": "Lille", "code": "59350", "population": 236710,
                                                "epci": {"code": "200093201", "nom": "Métropole Européenne de Lille"},
                                                "mairie": {"type": "Point", "coordinates": [3.0702, 50.6372]}}))
    vals, _ = T.fetch_geo("59350")
    assert vals["population"][0] == 236710 and vals["epci"][0].startswith("Métropole")
    assert vals["centre_lat"][0] == 50.6372 and vals["centre_lat"][1].confiance == "eleve"


def test_conso_enaf_colonnes_annuelles(mock):
    row = {"idcom": "59350", **{f"naf{a:02d}art{a+1:02d}": 20000 for a in range(9, 22)}}
    mock(lambda req: httpx.Response(200, json={"count": 1, "next": None, "results": [row]}))
    ha, diag = T.fetch_conso_enaf("59350")
    assert ha == 20.0 and diag["unite_supposee"].startswith("m²")  # 10 années (2011-2020) × 2 ha


def test_enrichissement_partiel_sans_planter(mock):
    def h(req):
        if "geo.api" in str(req.url) or "/communes/" in req.url.path and "indicateurs" not in req.url.path:
            return httpx.Response(200, json={"nom": "Lille", "population": 236710})
        return httpx.Response(503)
    mock(h)
    recs, rep = T.enrich_communes(["59350"])
    assert recs["59350"].get("population") == 236710
    assert len(rep["erreurs"]) >= 3 and not recs["59350"].has("zonage_abc")


def test_distance_mairie():
    assert abs(T.distance_km(50.6372, 3.0702, 50.6372, 3.0702)) < 1e-9
    assert 1.0 < T.distance_km(50.6372, 3.0702, 50.6166, 3.0745) < 2.6


# --------------------- formats observés sur les sources réelles (rapport du 26/11/2026)
LOVAC_RES = [
    {"title": "LOVAC open data 2020 à 2026 version xlsx", "format": "xlsx",
     "url": "https://static.data.gouv.fr/resources/logements-vacants-du-parc-prive-par-commune-departement-region-france/x/lovac.xlsx"},
    {"title": "Lovac_opendata_schema.csv", "format": "csv",
     "url": "https://static.data.gouv.fr/resources/logements-vacants-du-parc-prive-par-commune-departement-region-france/x/schema.csv"},
    {"title": "Lovac_opendata_Communes.csv", "format": "csv", "last_modified": "2026-06-25T10:00:00",
     "url": "https://static.data.gouv.fr/resources/logements-vacants-du-parc-prive-par-commune-departement-region-france/x/communes.csv"},
    {"title": "Lovac_opendata_Departements.csv", "format": "csv", "last_modified": "2026-06-25T10:00:00",
     "url": "https://static.data.gouv.fr/resources/logements-vacants-du-parc-prive-par-commune-departement-region-france/x/dep.csv"},
    {"title": "Lovac_opendata_France.csv", "format": "csv", "last_modified": "2026-06-25T16:37:00",
     "url": "https://static.data.gouv.fr/resources/logements-vacants-du-parc-prive-par-commune-departement-region-france/x/fr.csv"},
]


def test_lovac_choisit_le_fichier_des_communes(mock):
    mock(lambda req: httpx.Response(200, json={"resources": LOVAC_RES}))
    assert T.resolve_resource("lovac")["titre"] == "Lovac_opendata_Communes.csv"


def test_lovac_format_long_par_millesime(tmp_path):
    p = write(tmp_path, "l.csv", "CODGEO_25;LIBGEO_25;Millésime;ff_pp_total;pp_vacant;pp_vacant_plus_2ans\n"
                                 "59350;Lille;2024;120000;9000;3000\n59350;Lille;2026;125000;10000;3100\n"
                                 "59512;Roubaix;2026;40000;s;s\n59512;Roubaix;2025;40000;5200;2000\n")
    out, diag = T.parse_lovac(p, {"59350", "59512"})
    assert diag["colonne_code"] == "CODGEO_25" and diag["colonne_millesime"] == "Millésime"
    assert out == {"59350": 8.0, "59512": 13.0} and diag["annee"] == 2026  # Roubaix 2026 sous secret : millésime 2025


def test_colonne_millesime_jamais_prise_pour_un_code(tmp_path):
    p = write(tmp_path, "l.csv", "FR;Millésime;ff_pp_total;pp_vacant\nFrance;2024;1;1\nFrance;2025;1;1\n")
    with pytest.raises(K.ConnectorError):
        T.parse_lovac(p, {"59350"})


def test_dido_millesime_le_plus_recent(mock):
    seen = []

    def h(req):
        u = str(req.url)
        seen.append(u)
        if "/api/1/datasets/" in u:
            return httpx.Response(200, json={"resources": [{"title": "Logements autorisés et commencés par commune", "format": "csv",
                "url": "https://data.statistiques.developpement-durable.gouv.fr/dido/api/v1/datafiles/577a8a66-4157-4787-b00a-031b61afea61/csv"}]})
        if u.endswith("/datafiles/577a8a66-4157-4787-b00a-031b61afea61"):
            return httpx.Response(200, json={"rid": "577a8a66", "millesimes": [{"millesime": "2026-08", "rows": 1}, {"millesime": "2026-10", "rows": 1}]})
        assert req.url.params["millesime"] == "2026-10" and req.url.params["withColumnName"] == "true"
        return httpx.Response(200, text="DEP;COMM;DATE_PRISE_EN_COMPTE;LOG_AUT\n59;59350;2025-01;120\n")
    mock(h)
    path, meta = T.cached_file("sitadel")
    assert meta["millesime"] == "2026-10" and path.read_text().startswith("DEP;COMM")


def test_conso_enaf_lignes_annuelles_naf_arti(mock):
    rows = [{"annee": a, "conso_act": 1, "conso_hab": 2, "conso_inc": 0, "conso_infra": 0, "conso_mix": 0,
             "idcom": "59350", "idcomtxt": "Lille", "naf_arti": 30000} for a in range(2011, 2021)]
    mock(lambda req: httpx.Response(200, json={"count": 10, "next": None, "results": rows}))
    ha, diag = T.fetch_conso_enaf("59350")
    assert diag["colonne_total"] == "naf_arti" and ha == 30.0 and diag["annees"] == list(range(2011, 2021))


def test_cache_d_une_ancienne_version_ignore(mock, tmp_path):
    (tmp_path / "lovac.json").write_text(json.dumps({"fichier": "lovac.csv", "telecharge_le_ts": T.time.time(), "titre": "Lovac_opendata_France.csv"}))
    (tmp_path / "lovac.csv").write_text("FR;Millésime\n")
    mock(lambda req: httpx.Response(200, json={"resources": LOVAC_RES}) if "/api/1/" in str(req.url)
         else httpx.Response(200, text="CODGEO_25;Millésime;ff_pp_total;pp_vacant\n59350;2026;100;8\n"))
    _p, meta = T.cached_file("lovac")
    assert meta["titre"] == "Lovac_opendata_Communes.csv" and meta["version"] == T.CACHE_VERSION


def test_lovac_format_reel_2026_annees_decalees(tmp_path):
    """Format réel 2026 : pp_vacant_26 publié sans ff_pp_total_26 -> on prend 2025, jamais 2026/2025 mélangés."""
    p = write(tmp_path, "l.csv", "CODGEO_26;LIBGEO_26;pp_vacant_26;pp_vacant_plus_2ans_26;ff_pp_total_25;pp_vacant_25;pp_vacant_plus_2ans_25;"
                                 "ff_pp_total_24;pp_vacant_24;pp_vacant_plus_2ans_24;EPCI_26;DEP\n"
                                 "59350;Lille;15000;3000;120000;9600;3000;118000;9000;2900;200093201;59\n"
                                 "59512;Roubaix;s;s;40000;s;s;40000;5200;2000;200093201;59\n")
    out, diag = T.parse_lovac(p, {"59350", "59512"})
    assert diag["colonne_vacants"] == "pp_vacant_25" and diag["colonne_total"] == "ff_pp_total_25"
    assert out == {"59350": 8.0, "59512": 13.0} and diag["annee"] == 2025  # Roubaix : repli sur 2024 (secret en 2025)


def test_dido_millesime_pas_encore_publie(mock):
    def h(req):
        u = str(req.url)
        if "/api/1/datasets/" in u:
            return httpx.Response(200, json={"resources": [{"title": "Logements autorisés par commune", "format": "csv",
                "url": "https://dido.test/dido/api/v1/datafiles/577a8a66-4157-4787-b00a-031b61afea61/csv"}]})
        if u.endswith("/datafiles/577a8a66-4157-4787-b00a-031b61afea61"):
            return httpx.Response(200, json={"millesimes": [{"millesime": "2026-10"}, {"millesime": "2026-11"}]})
        if req.url.params["millesime"] == "2026-11":
            return httpx.Response(400, json={"code": 400, "message": "Les données de ce millésime ne seront pas disponible avant 2026-11-27T07:45:00.000Z"})
        return httpx.Response(200, text="DEP;COMM;DATE_PRISE_EN_COMPTE;LOG_AUT\n59;59350;2025-01;120\n")
    mock(h)
    _p, meta = T.cached_file("sitadel")
    assert meta["millesime"] == "2026-10" and "2026-11" in meta["millesimes_ignores"][0]
