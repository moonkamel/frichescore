"""Indicateurs communaux réels : API Géo, zonage ABC, LOVAC, SITADEL, consommation d'ENAF.

Principes :
  * les fichiers nationaux (zonage ABC, LOVAC, SITADEL) ne sont pas désignés par une adresse figée :
    leur adresse courante est obtenue via l'API de data.gouv.fr (jeu de données -> ressource la
    plus pertinente), puis le fichier est téléchargé UNE fois et mis en cache à côté de la base ;
  * les colonnes sont détectées de façon tolérante (le format change d'un millésime à l'autre) et
    la détection est exposée dans le test de connexion pour être vérifiée ;
  * chaque indicateur est indépendant : un échec n'empêche pas les autres et n'invente rien.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import statistics
import time
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

from ..scoring.record import Provenance, Record
from . import connectors as K

GEO_API_URL = os.environ.get("GEO_API_URL", "https://geo.api.gouv.fr")
DATAGOUV_API = os.environ.get("DATAGOUV_API", "https://www.data.gouv.fr/api/1")
CEREMA_BASE = os.environ.get("CEREMA_API_URL", "https://apidf-preprod.cerema.fr")
CACHE_MAX_AGE_DAYS = int(os.environ.get("FRICHESCORE_CACHE_JOURS", "30"))
CACHE_VERSION = 2  # à incrémenter quand la règle de choix des fichiers change : les caches antérieurs sont ignorés

# Jeux de données data.gouv.fr (identifiant « slug ») et critères de choix de la ressource
DATASETS = {
    "zonage_abc": {"slug": "liste-des-communes-selon-le-zonage-abc", "label": "Zonage ABC",
                   "prefer": [r"ensemble des communes", r"liste.*communes"], "avoid": [r"reclass", r"d[ée]class", r"dictionnaire"]},
    "lovac": {"slug": "logements-vacants-du-parc-prive-par-commune-departement-region-france", "label": "LOVAC (logements vacants)",
              "prefer": [r"commune"], "avoid": [r"departement", r"region", r"france", r"epci", r"schema", r"dictionnaire", r"documentation"]},
    "sitadel": {"slug": "logements-autorises-et-commences-series-mensuelles-communales-en-date-de-prise-en-compte",
                "label": "SITADEL (logements autorisés)", "prefer": [r"commun"], "avoid": [r"dictionnaire", r"documentation", r"m[ée]tadonn"]},
}


def cache_dir() -> Path:
    from ..db import DB_PATH
    d = DB_PATH.parent / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn").strip().lower()


# --------------------------------------------------------------- API Géo
def fetch_geo(code: str) -> tuple[dict, dict]:
    """Population, EPCI, mairie. Retourne (valeurs, diagnostic)."""
    url = f"{GEO_API_URL.rstrip('/')}/communes/{code}"
    params = {"fields": "nom,code,population,epci,codeEpci,codeDepartement,mairie,centre,surface", "format": "json"}
    diag = {"source": "API Géo", "url": None, "statut": None, "erreur": None, "detail": None}
    with K._client(30) as c:
        data, full, st = K._get(c, url, params)
    diag.update(url=full, statut=st, detail=data)
    today = date.today().isoformat()
    out = {}
    if isinstance(data, dict):
        if data.get("population") is not None:
            out["population"] = (int(data["population"]), Provenance("INSEE via API Géo", today, "eleve", "population municipale"))
        epci = data.get("epci")
        if isinstance(epci, dict) and epci.get("nom"):
            out["epci"] = (epci["nom"], Provenance("BANATIC via API Géo", today, "eleve"))
        pt = data.get("mairie") or data.get("centre")
        if isinstance(pt, dict) and pt.get("coordinates"):
            lon, lat = pt["coordinates"][:2]
            which = "mairie" if data.get("mairie") else "centre géométrique de la commune"
            p = Provenance("API Géo", today, "eleve" if data.get("mairie") else "moyen", which)
            out["centre_lat"] = (float(lat), p)
            out["centre_lon"] = (float(lon), p)
        if data.get("nom"):
            out["commune"] = (data["nom"], Provenance("COG via API Géo", today, "eleve"))
    return out, diag


# ------------------------------------------------- fichiers data.gouv.fr
def resolve_resource(name: str) -> dict:
    """Ressource la plus pertinente d'un jeu de données data.gouv.fr (adresse, titre, date)."""
    cfg = DATASETS[name]
    with K._client(30) as c:
        data, _f, _s = K._get(c, f"{DATAGOUV_API.rstrip('/')}/datasets/{cfg['slug']}/")
    res = [r for r in (data.get("resources") or []) if r.get("url")]
    if not res:
        raise K.ConnectorError(f"aucune ressource dans le jeu de données « {cfg['slug']} »")

    def score(r):
        # Le titre seul : l'adresse contient le nom du jeu de données (« …commune-departement-region-france »)
        t = _norm(r.get("title", ""))
        u = _norm(r.get("url", "")).split("?")[0]
        fmt = _norm(r.get("format") or "")
        sc = 0
        sc += 10 if fmt in ("csv", "zip") or t.endswith((".csv", ".zip")) or u.endswith((".csv", ".zip")) or "/dido/" in u else -20
        sc -= 20 if t.endswith((".xlsx", ".xls", ".pdf")) or fmt in ("xlsx", "xls", "pdf") else 0
        sc += sum(5 for p in cfg["prefer"] if re.search(p, t))
        sc -= sum(15 for p in cfg["avoid"] if re.search(p, t))
        return (sc, r.get("last_modified") or r.get("created_at") or "")
    best = max(res, key=score)
    return {"url": best["url"], "titre": best.get("title"), "date": (best.get("last_modified") or "")[:10],
            "format": best.get("format"), "taille": best.get("filesize"),
            "candidats": [r.get("title") for r in res][:12]}


DIDO = re.compile(r"(https?://[^/]+/dido/api/v1)/datafiles/([0-9a-f-]{20,})", re.I)


def dido_params(url: str) -> tuple[str, dict, dict]:
    """L'API DiDo (SDES) exige un millésime : on lit les métadonnées du fichier et on prend le plus récent."""
    m = DIDO.search(url)
    if not m:
        return url, {}, {}
    base, rid = m.group(1), m.group(2)
    with K._client(60) as c:
        meta, _f, _s = K._get(c, f"{base}/datafiles/{rid}")
    mills = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower().startswith("millesime") and isinstance(v, str) and re.match(r"\d{4}", v):
                    mills.append(v)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(meta)
    if not mills:
        raise K.ConnectorError("API DiDo : aucun millésime trouvé dans les métadonnées du fichier", f"{base}/datafiles/{rid}")
    ordered = sorted(set(mills), reverse=True)
    # Les métadonnées peuvent ne lister que le millésime annoncé (pas encore publié) : on ajoute les
    # mois précédents comme solutions de repli (format AAAA-MM).
    m2 = re.fullmatch(r"(\d{4})-(\d{2})", ordered[0])
    if m2:
        y, mo = int(m2.group(1)), int(m2.group(2))
        for _ in range(3):
            y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
            cand = f"{y}-{mo:02d}"
            if cand not in ordered:
                ordered.append(cand)
        ordered = sorted(set(ordered), reverse=True)
    return (f"{base}/datafiles/{rid}/csv",
            {"millesime": ordered[0], "withColumnName": "true", "withColumnDescription": "false", "withColumnUnit": "false"},
            {"millesime": ordered[0], "millesimes_disponibles": ordered[:6]})


def _download(url: str, dest: Path, params: dict | None = None) -> None:
    tmp = dest.with_suffix(".part")
    with K._client(180) as c:
        try:
            with c.stream("GET", url, params=params) as r:
                if r.status_code >= 400:
                    body = r.read().decode("utf-8", errors="replace")[:300]
                    raise K.ConnectorError(f"téléchargement refusé (HTTP {r.status_code}) : {body}", str(r.request.url), r.status_code)
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
        except K.httpx.HTTPError as e:
            raise K.ConnectorError(f"téléchargement interrompu ({type(e).__name__}) : {str(e)[:120]}", url)
    tmp.replace(dest)


def cached_file(name: str, refresh: bool = False) -> tuple[Path, dict]:
    """Télécharge (si nécessaire) le fichier national et renvoie (chemin, métadonnées)."""
    d = cache_dir()
    meta_p = d / f"{name}.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else None
    fresh = (meta and meta.get("version") == CACHE_VERSION
             and (time.time() - meta.get("telecharge_le_ts", 0)) < CACHE_MAX_AGE_DAYS * 86400)
    path = d / (meta or {}).get("fichier", f"{name}.bin")
    if meta and fresh and path.exists() and not refresh:
        return path, meta
    info = resolve_resource(name)
    ext = ".zip" if info["url"].lower().split("?")[0].endswith(".zip") or _norm(info.get("format")) == "zip" else ".csv"
    path = d / f"{name}{ext}"
    url, params, extra = dido_params(info["url"])
    if extra.get("millesimes_disponibles"):
        # Le millésime le plus récent peut être annoncé avant d'être publié (« pas disponible avant … ») :
        # on essaie les plus récents, du plus neuf au plus ancien.
        last_err = None
        for mil in extra["millesimes_disponibles"][:4]:
            try:
                _download(url, path, {**params, "millesime": mil})
                extra["millesime"] = mil
                last_err = None
                break
            except K.ConnectorError as e:
                last_err = e
                extra.setdefault("millesimes_ignores", []).append(f"{mil} : {e.message[:120]}")
                if e.status not in (400, 404, 409, 425):
                    break
        if last_err:
            raise last_err
        info.update(extra)
        info["date"] = extra["millesime"]
    else:
        _download(url, path, params)
    meta = {**info, "fichier": path.name, "telecharge_le": date.today().isoformat(), "telecharge_le_ts": time.time(),
            "version": CACHE_VERSION}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return path, meta


def _open_text(path: Path):
    """Ouvre le fichier (CSV ou premier CSV d'un ZIP) en lecture CONTINUE, encodage détecté sur le début."""
    if path.suffix == ".zip":
        z = zipfile.ZipFile(path)
        names = [n for n in z.namelist() if n.lower().endswith(".csv")] or z.namelist()
        names.sort(key=lambda n: -z.getinfo(n).file_size)
        with z.open(names[0]) as f:
            head = f.read(65536)
        enc = _guess_enc(head)
        return io.TextIOWrapper(z.open(names[0]), encoding=enc, errors="replace", newline="")
    with open(path, "rb") as f:
        head = f.read(65536)
    return open(path, encoding=_guess_enc(head), errors="replace", newline="")


def _guess_enc(head: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError as e:
            if enc.startswith("utf-8") and e.start > len(head) - 4:  # caractère coupé en fin d'extrait
                return enc
    return "latin-1"


def _reader(path: Path) -> tuple[csv.DictReader, list[str]]:
    f = _open_text(path)
    first = f.readline()
    f = _open_text(path)  # réouverture : les flux ZIP ne permettent pas de revenir au début
    delim = max(";,\t|", key=first.count) if first else ";"
    r = csv.DictReader(f, delimiter=delim)
    return r, [h for h in (r.fieldnames or []) if h]


CODE_COL = re.compile(r"^(codgeo|code_?insee|insee_?com|code_?commune|com(_?code)?|code_?com|depcom|cog|code)(_\d+)?$", re.I)


def find_code_column(headers: list[str], rows: list[dict]) -> str | None:
    """Colonne du code commune. Un code à 4 chiffres (zéro initial perdu) n'est accepté que dans une colonne
    NOMMÉE comme un code : sinon une colonne d'années (« 2020 » -> « 02020 ») serait prise pour un code."""
    cands = [h for h in headers if CODE_COL.match(_norm(h).replace(" ", "_"))]
    for h in cands + [h for h in headers if h not in cands]:
        named = h in cands
        vals = [str(r.get(h) or "").strip() for r in rows[:200]]
        ok = sum(1 for v in vals if re.fullmatch(r"(\d{5}|2[AB]\d{3})", _code(v) if named else v))
        if vals and ok / len(vals) > 0.8:
            return h
    return None


def _code(v) -> str:
    v = str(v or "").strip()
    return v.zfill(5) if v.isdigit() and len(v) == 4 else v


ABC = {"abis": "Abis", "a bis": "Abis", "a": "A", "b1": "B1", "b2": "B2", "c": "C"}


def parse_zonage(path: Path, codes: set[str]) -> tuple[dict, dict]:
    reader, headers = _reader(path)
    rows = list(reader)
    code_col = find_code_column(headers, rows)
    zone_col = None
    for h in headers:
        vals = {_norm(r.get(h) or "") for r in rows[:500]} - {""}
        if vals and vals <= set(ABC):
            zone_col = h
            break
    diag = {"colonnes": headers[:30], "colonne_code": code_col, "colonne_zone": zone_col, "lignes": len(rows)}
    if not code_col or not zone_col:
        raise K.ConnectorError(f"colonnes non identifiées dans le fichier du zonage ABC (code : {code_col}, zone : {zone_col})")
    out = {}
    for r in rows:
        c = _code(r.get(code_col))
        if c in codes:
            z = ABC.get(_norm(r.get(zone_col) or ""))
            if z:
                out[c] = z
    return out, diag


def _num(v):
    try:
        return float(str(v).replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_lovac(path: Path, codes: set[str]) -> tuple[dict, dict]:
    """Taux de vacance du parc privé (LOVAC). Deux formats acceptés :
      * « long » (format observé en 2026) : une ligne par commune et par millésime, colonnes
        Millésime / ff_pp_total / pp_vacant / pp_vacant_plus_2ans -> on garde le millésime le plus récent ;
      * « large » : une colonne par année (pp_vacant_25, pp_total_25…) -> l'année la plus récente."""
    reader, headers = _reader(path)
    rows = list(reader)
    code_col = find_code_column(headers, rows)
    nh = {h: _norm(h) for h in headers}
    longue = re.compile(r"(2_?ans|plus|long|fil|duree)")  # vacance de longue durée : on veut la vacance totale
    year_col = next((h for h in headers if re.fullmatch(r"(millesime|annee|an|year)", nh[h])), None)

    def year(h):
        m = re.search(r"(20\d{2}|\d{2})$", nh[h])
        if not m:
            return 0
        y = int(m.group(1))
        return y + 2000 if y < 100 else y
    rate = [h for h in headers if "taux" in nh[h] and "vac" in nh[h] and not longue.search(nh[h])]
    vac = [h for h in headers if "vacant" in nh[h] and "taux" not in nh[h] and not longue.search(nh[h])]
    tot = [h for h in headers if ("total" in nh[h] or "parc" in nh[h]) and "vac" not in nh[h]]
    rate_col = max(rate, key=year) if rate else None
    # Couples (vacants, parc total) de MÊME année, du plus récent au plus ancien : le millésime le plus récent
    # peut publier les vacants avant le parc total (constaté en 2026 : pp_vacant_26 sans ff_pp_total_26).
    tot_by_year = {year(h): h for h in tot}
    pairs = sorted(((year(v), v, tot_by_year[year(v)]) for v in vac if year(v) in tot_by_year), reverse=True)
    if year_col and not pairs and vac and tot:  # format long : une seule colonne de chaque
        pairs = [(0, vac[0], tot[0])]
    diag = {"colonnes": headers[:40], "colonne_code": code_col, "colonne_millesime": year_col, "colonne_taux": rate_col,
            "colonne_vacants": pairs[0][1] if pairs else None, "colonne_total": pairs[0][2] if pairs else None,
            "lignes": len(rows)}
    if not code_col or not (rate_col or pairs):
        raise K.ConnectorError("colonnes non identifiées dans le fichier LOVAC (code commune, taux ou vacants/total de même année) — "
                               "le fichier retenu n'est peut-être pas celui des communes")
    best: dict[str, tuple] = {}
    for r in rows:
        c = _code(r.get(code_col))
        if c not in codes:
            continue
        v, y = None, 0
        if rate_col:
            v = _num(r.get(rate_col))
            if v is not None and v <= 1:
                v *= 100
            y = year(rate_col)
        if v is None:
            for py, vc, tc in pairs:  # repli : année précédente si la plus récente est sous secret statistique
                a_, b_ = _num(r.get(vc)), _num(r.get(tc))
                if a_ is not None and b_:
                    v, y = 100 * a_ / b_, py
                    break
        if v is None or not (0 <= v <= 60):
            continue
        if year_col:
            m = re.search(r"(19|20)\d{2}", str(r.get(year_col) or ""))
            y = int(m.group(0)) if m else y
        if c not in best or y >= best[c][0]:
            best[c] = (y, round(v, 1))
    out = {c: v for c, (y, v) in best.items()}
    ys = {y for y, _v in best.values() if y}
    diag["annee"] = max(ys) if ys else None
    return out, diag


def parse_sitadel(path: Path, codes: set[str], years: int = 3) -> tuple[dict, dict]:
    """Moyenne annuelle des logements autorisés sur les `years` dernières années complètes."""
    reader, headers = _reader(path)
    nh = {h: _norm(h) for h in headers}
    log_col = next((h for h in headers if re.fullmatch(r"log_?aut(ori[sz]es?)?", nh[h].replace(" ", "_"))), None) \
        or next((h for h in headers if "aut" in nh[h] and "log" in nh[h] and "surf" not in nh[h]), None)
    date_col = next((h for h in headers if re.search(r"date|annee|^an$|periode|mois", nh[h])), None)
    buf = []
    for i, r in enumerate(reader):
        buf.append(r)
        if i >= 300:
            break
    code_col = find_code_column(headers, buf)
    dep_col = com_col = None
    if not code_col:  # code en deux colonnes : département + commune
        dep_col = next((h for h in headers if re.fullmatch(r"dep(artement)?(_code)?", nh[h])), None)
        com_col = next((h for h in headers if re.fullmatch(r"com(mune)?(_code)?", nh[h])), None)
    diag = {"colonnes": headers[:40], "colonne_code": code_col or f"{dep_col}+{com_col}", "colonne_logements": log_col,
            "colonne_date": date_col}
    if not log_col or not date_col or not (code_col or (dep_col and com_col)):
        raise K.ConnectorError("colonnes non identifiées dans le fichier SITADEL (code commune, date, logements autorisés)")
    per: dict[str, dict[int, float]] = {}
    n = 0

    def handle(r):
        nonlocal n
        n += 1
        c = _code(r.get(code_col)) if code_col else (str(r.get(dep_col) or "").zfill(2) + str(r.get(com_col) or "").zfill(3))[-5:]
        if c not in codes:
            return
        m = re.search(r"(19|20)\d{2}", str(r.get(date_col) or ""))
        v = _num(r.get(log_col))
        if m and v is not None:
            per.setdefault(c, {}).setdefault(int(m.group(0)), 0.0)
            per[c][int(m.group(0))] += v
    for r in buf:
        handle(r)
    for r in reader:
        handle(r)
    diag["lignes"] = n
    last_full = date.today().year - 1
    out, yrs = {}, list(range(last_full - years + 1, last_full + 1))
    for c, d in per.items():
        avail = [y for y in yrs if y in d]
        if avail:
            out[c] = sum(d[y] for y in avail) / len(avail)
    diag["annees"] = yrs
    return out, diag


# ------------------------------------------------- SITADEL filtré (DiDo)
def dido_info(url: str) -> dict:
    """Métadonnées d'un fichier DiDo : base, identifiant, millésimes (avec repli sur les mois précédents), colonnes."""
    m = DIDO.search(url)
    if not m:
        raise K.ConnectorError("la ressource SITADEL n'est pas servie par l'API DiDo", url)
    base, rid = m.group(1), m.group(2)
    _u, params, extra = dido_params(url)
    with K._client(60) as c:
        meta, _f, _s = K._get(c, f"{base}/datafiles/{rid}")
    cols = []

    def walk(o, under_col=False):
        if isinstance(o, dict):
            if under_col and isinstance(o.get("name"), str):
                cols.append(o["name"])
            for k, v in o.items():
                walk(v, under_col or "column" in k.lower())
        elif isinstance(o, list):
            for x in o:
                walk(x, under_col)
    walk(meta)
    return {"base": base, "rid": rid, "millesimes": extra.get("millesimes_disponibles", []), "colonnes": list(dict.fromkeys(cols))}


def _pick(cols, *patterns):
    for pat in patterns:
        for c in cols:
            if re.fullmatch(pat, _norm(c)):
                return c
    return None


SITADEL_CACHE = "sitadel_communes.json"


def sitadel_values(codes: set[str], refresh: bool = False, years: int = 3) -> tuple[dict, dict]:
    """Logements autorisés (moyenne annuelle des `years` dernières années complètes) pour les seules communes
    demandées, via les filtres de l'API DiDo — sans télécharger le fichier national."""
    cache_p = cache_dir() / SITADEL_CACHE
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    now = time.time()
    todo = sorted(c for c in codes if refresh or c not in cache or now - cache[c].get("ts", 0) > CACHE_MAX_AGE_DAYS * 86400)
    diag = {"communes_en_cache": len(codes) - len(todo), "communes_interrogees": len(todo)}
    if todo:
        res = resolve_resource("sitadel")
        info = dido_info(res["url"])
        cols = info["colonnes"]
        code_col = _pick(cols, r"(depcom|codgeo|code_?insee|code_?commune|insee_?com|com_?code|cog)", r"comm?(une)?")
        dep_col = _pick(cols, r"dep(artement)?(_code)?")
        date_col = _pick(cols, r"date.*", r"(annee|an|periode|mois)")
        log_col = _pick(cols, r"log_?aut(ori[sz]es?)?") or next((c for c in cols if "aut" in _norm(c) and "log" in _norm(c) and "surf" not in _norm(c)), None)
        diag.update({"ressource": res.get("titre"), "colonnes": cols[:30], "colonne_code": code_col, "colonne_departement": dep_col,
                     "colonne_date": date_col, "colonne_logements": log_col})
        if not (code_col and date_col and log_col):
            raise K.ConnectorError(f"SITADEL : colonnes non identifiées dans les métadonnées DiDo ({', '.join(cols[:15]) or 'aucune'})")
        url = f"{info['base']}/datafiles/{info['rid']}/csv"
        keep = ",".join(c for c in (dep_col, code_col, date_col, log_col) if c)
        base_params = {"withColumnName": "true", "withColumnDescription": "false", "withColumnUnit": "false", "columns": keep}
        # Le code commune peut tenir sur 3 caractères (avec une colonne département) ou 5 : on essaie les deux.
        five = {c: c for c in todo}
        three = {c[2:] if not c.startswith("97") else c[3:]: c for c in todo}
        attempts = [("in", code_col, five), ("in", code_col, three)]
        if dep_col:
            deps = sorted({c[:3] if c.startswith("97") else c[:2] for c in todo})
            attempts.append(("dep", dep_col, {d: d for d in deps}))
        rows, used, errs = None, None, []
        for mil in info["millesimes"][:4] or [None]:
            p_ = dict(base_params)
            if mil:
                p_["millesime"] = mil
            for kind, col, mapping in attempts:
                vals = sorted(mapping)
                q = dict(p_)
                q[col] = ("in:" + ",".join(vals)) if len(vals) > 1 else f"eq:{vals[0]}"
                try:
                    with K._client(120) as c:
                        txt, full, _st = K._get(c, url, q, expect="text")
                except K.ConnectorError as e:
                    errs.append(f"{mil} {col}={q[col][:40]} : {e.message[:140]}")
                    if e.status not in (400, 404, 409, 425):
                        raise
                    continue
                rd = list(csv.DictReader(io.StringIO(txt.lstrip("\ufeff")), delimiter=";" if txt.count(";") >= txt.count(",") else ","))
                if rd:
                    rows, used = rd, {"millesime": mil, "filtre": f"{col}={q[col][:60]}", "url": full, "lignes": len(rd)}
                    break
            if rows is not None:
                break
        diag["essais_en_echec"] = errs[-6:]
        if rows is None:
            raise K.ConnectorError("SITADEL : aucune ligne renvoyée par l'API DiDo pour ces communes — " + " | ".join(errs[-3:]))
        diag.update(used)
        per: dict[str, dict] = {c: {} for c in todo}
        for r in rows:
            raw = str(r.get(code_col) or "").strip()
            dep = str(r.get(dep_col) or "").strip() if dep_col else ""
            code = raw if len(raw) == 5 else (dep.zfill(2) + raw.zfill(3) if dep else raw)
            if code not in per:
                continue
            m = re.search(r"(19|20)\d{2}", str(r.get(date_col) or ""))
            v = _num(r.get(log_col))
            if m and v is not None:
                per[code][m.group(0)] = per[code].get(m.group(0), 0.0) + v
        for c in todo:
            cache[c] = {"annees": per[c], "ts": now, "millesime": used.get("millesime")}
        cache_p.write_text(json.dumps(cache), encoding="utf-8")
    last_full = date.today().year - 1
    yrs = [str(y) for y in range(last_full - years + 1, last_full + 1)]
    out = {}
    for c in codes:
        d = (cache.get(c) or {}).get("annees") or {}
        avail = [y for y in yrs if y in d]
        if avail:
            out[c] = sum(d[y] for y in avail) / len(avail)
    diag["annees"] = [int(y) for y in yrs]
    return out, diag


# --------------------------------------------- consommation d'ENAF (Cerema)
def fetch_conso_enaf(code: str) -> tuple[float | None, dict]:
    """Consommation d'espaces NAF 2011-2021 (ha) via l'API Cerema /indicateurs/conso_espace."""
    url = f"{CEREMA_BASE.rstrip('/')}/indicateurs/conso_espace/communes/{code}"
    with K._client(60) as c:
        data, full, st = K._get(c, url, {"annee_min": 2011, "annee_max": 2020, "page_size": 100})
    rows = K.parse_cartofriches(data) if not isinstance(data, list) else data
    diag = {"url": full, "statut": st, "lignes": len(rows), "colonnes": sorted(rows[0].keys())[:40] if rows else []}
    if not rows:
        return None, diag
    keys = rows[0].keys()
    total_col = next((k for k in keys if re.fullmatch(r"(naf_?arti?|conso_?(tot(ale?)?)?|conso_naf)", _norm(k))), None) \
        or next((k for k in keys if "conso" in _norm(k) and "tot" in _norm(k)), None)
    diag["colonne_total"] = total_col
    if total_col:
        # une ligne par année (format observé : annee, naf_arti, conso_hab, conso_act…) ; on borne à 2011-2020
        def yr(r):
            m = re.search(r"(19|20)\d{2}", str(r.get("annee") or ""))
            return int(m.group(0)) if m else None
        sel = [r for r in rows if yr(r) is None or 2011 <= yr(r) <= 2020]
        diag["annees"] = sorted({yr(r) for r in sel if yr(r)})
        tot = sum(v for v in (_num(r.get(total_col)) for r in sel) if v)
    else:  # une colonne par année : naf11art12, naf12art13, ...
        cols = [k for k in keys if re.fullmatch(r"naf\d{2}art\d{2}", _norm(k))]
        diag["colonnes_annuelles"] = cols
        if not cols:
            return None, diag
        tot = sum(_num(rows[0].get(k)) or 0 for k in cols if 11 <= int(_norm(k)[3:5]) <= 20)
    ha = tot / 10_000 if tot > 5_000 else tot  # valeurs en m² au-delà de ce seuil (hypothèse vérifiable dans le diagnostic)
    diag["unite_supposee"] = "m² convertis en ha" if tot > 5_000 else "ha"
    return round(ha, 1), diag


# ------------------------------------------------------------ orchestration
def enrich_communes(codes: list[str], existing: dict[str, Record] | None = None, refresh: bool = False,
                    progress=None) -> tuple[dict[str, Record], dict]:
    """Calcule les indicateurs communaux réels. Retourne ({code: Record}, rapport)."""
    existing = existing or {}
    codes = sorted({c for c in codes if c})
    today = date.today().isoformat()
    recs = {c: Record() for c in codes}
    rep = {"communes": len(codes), "indicateurs": {}, "erreurs": [], "fichiers": {}}

    def tick(e, f=0, t=0):
        if progress:
            progress(e, f, t)

    def setv(c, k, v, p):
        recs[c].set(k, v, p)
        rep["indicateurs"][k] = rep["indicateurs"].get(k, 0) + 1

    for i, c in enumerate(codes):
        tick("API Géo : population, EPCI, mairie", i, len(codes))
        try:
            vals, _ = fetch_geo(c)
            for k, (v, p) in vals.items():
                setv(c, k, v, p)
        except K.ConnectorError as e:
            rep["erreurs"].append(f"{c} : API Géo — {e.message}")
    cs = set(codes)
    for name, parser in (("zonage_abc", parse_zonage), ("lovac", parse_lovac), ("sitadel", None)):
        tick(f"{DATASETS[name]['label']}", 0, 1)
        try:
            if name == "sitadel":  # interrogation filtrée de l'API DiDo (pas de fichier national)
                vals, diag = sitadel_values(cs, refresh)
                meta = {"titre": diag.get("ressource") or "API DiDo (SDES)", "date": diag.get("millesime"),
                        "telecharge_le": date.today().isoformat()}
            else:
                path, meta = cached_file(name, refresh)
                vals, diag = parser(path, cs)
            rep["fichiers"][name] = {"titre": meta.get("titre"), "date": meta.get("date"), "telecharge_le": meta.get("telecharge_le"),
                                     "communes_trouvees": len(vals), **{k: v for k, v in diag.items() if k != "colonnes"}}
            src = f"{DATASETS[name]['label']} — data.gouv.fr ({meta.get('titre') or ''})"
            d = meta.get("date") or today
            for c, v in vals.items():
                if name == "zonage_abc":
                    setv(c, "zonage_abc", v, Provenance(src, d, "eleve"))
                elif name == "lovac":
                    setv(c, "vacance_logement_pct", v, Provenance(src, str(diag.get("annee") or d), "moyen",
                                                                  "vacance du parc privé (LOVAC)"))
                else:
                    pop = (recs[c].get("population") or (existing.get(c).get("population") if existing.get(c) else None))
                    if pop:
                        setv(c, "logements_autorises_1000hab", round(v / pop * 1000, 1),
                             Provenance(src, d, "eleve", f"moyenne annuelle {diag['annees'][0]}-{diag['annees'][-1]} rapportée à la population"))
        except K.ConnectorError as e:
            rep["erreurs"].append(f"{DATASETS[name]['label']} : {e.message}")
        except (OSError, csv.Error, zipfile.BadZipFile) as e:
            rep["erreurs"].append(f"{DATASETS[name]['label']} : fichier illisible ({type(e).__name__})")
    for i, c in enumerate(codes):
        tick("Cerema : consommation d'espaces NAF", i, len(codes))
        try:
            ha, _ = fetch_conso_enaf(c)
            if ha is not None:
                setv(c, "conso_enaf_ha_2011_2021", ha, Provenance("Cerema — API Données foncières (conso_espace)", today, "eleve"))
        except K.ConnectorError as e:
            rep["erreurs"].append(f"{c} : consommation ENAF — {e.message}")
    return recs, rep


def distance_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def test_communes(code: str, refresh: bool = False) -> dict:
    """Diagnostic complet pour une commune : chaque source, son adresse, la détection des colonnes et la valeur obtenue."""
    out = {}
    t0 = time.monotonic()
    try:
        vals, diag = fetch_geo(code)
        out["api_geo"] = {"ok": True, "url": diag["url"], "valeurs": {k: v for k, (v, _p) in vals.items()}}
    except K.ConnectorError as e:
        out["api_geo"] = {"ok": False, "erreur": e.message, "url": e.url}
    for name, parser in (("zonage_abc", parse_zonage), ("lovac", parse_lovac), ("sitadel", None)):
        try:
            if name == "sitadel":
                vals, diag = sitadel_values({code}, refresh)
                meta = {"titre": diag.get("ressource"), "date": diag.get("millesime"), "url": diag.get("url")}
                out[name] = {"ok": code in vals, "ressource": meta, "detection": diag,
                             "valeur": round(vals[code], 1) if code in vals else None,
                             **({} if code in vals else {"erreur": "aucune donnée pour cette commune sur les 3 dernières années"})}
                continue
            path, meta = cached_file(name, refresh)
            vals, diag = parser(path, {code})
            out[name] = {"ok": code in vals, "ressource": {k: meta.get(k) for k in ("titre", "date", "url", "telecharge_le", "candidats")},
                         "detection": diag, "valeur": vals.get(code),
                         **({} if code in vals else {"erreur": "commune absente du fichier (secret statistique ou code non trouvé)"})}
        except K.ConnectorError as e:
            out[name] = {"ok": False, "erreur": e.message, "url": e.url}
        except (OSError, csv.Error, zipfile.BadZipFile) as e:
            out[name] = {"ok": False, "erreur": f"fichier illisible ({type(e).__name__}: {e})"}
    try:
        ha, diag = fetch_conso_enaf(code)
        out["conso_enaf"] = {"ok": ha is not None, "valeur_ha": ha, "detection": diag}
    except K.ConnectorError as e:
        out["conso_enaf"] = {"ok": False, "erreur": e.message, "url": e.url}
    ok = [k for k, v in out.items() if v.get("ok")]
    return {"source": "communes", "ok": bool(ok), "duree_ms": round((time.monotonic() - t0) * 1000), "code_insee": code,
            "resultat": {"sources_ok": ok, "sources_en_echec": [k for k in out if k not in ok]}, "detail": out,
            "erreur": None if ok else "aucune source d'indicateurs communaux n'a répondu"}
