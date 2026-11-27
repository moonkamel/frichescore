"""Persistance SQLite.

Isolation entre organismes (voir tenant.py) : les friches sans organisme (`org` NULL) et les communes forment la
base commune ; toutes les autres données portent l'organisme qui les a créées et ne sont lues que par lui.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import tenant
from .scoring.record import Provenance, Record

DB_PATH = Path(os.environ.get("FRICHESCORE_DB", Path(__file__).resolve().parent.parent / "data" / "frichescore.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS communes (code_insee TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS friches (
    id TEXT PRIMARY KEY, code_insee TEXT, departement TEXT, epci TEXT, origine TEXT,
    data TEXT NOT NULL, created_at TEXT, org TEXT);
CREATE INDEX IF NOT EXISTS ix_friches_dep ON friches(departement);
CREATE INDEX IF NOT EXISTS ix_friches_epci ON friches(epci);
CREATE TABLE IF NOT EXISTS field_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT, friche_id TEXT, key TEXT, value TEXT,
    source TEXT, date TEXT, confiance TEXT, auteur TEXT, created_at TEXT, org TEXT);
CREATE TABLE IF NOT EXISTS portfolio (
    friche_id TEXT, stage TEXT, usage TEXT, notes TEXT, updated_at TEXT, org TEXT NOT NULL DEFAULT 'local',
    PRIMARY KEY (org, friche_id));
CREATE TABLE IF NOT EXISTS parametres (
    path TEXT, value TEXT NOT NULL, note TEXT, updated_at TEXT, org TEXT NOT NULL DEFAULT 'local', PRIMARY KEY (org, path));
CREATE TABLE IF NOT EXISTS ia_extractions (
    id TEXT PRIMARY KEY, friche_id TEXT, fichier TEXT, sha256 TEXT, taille INTEGER, created_at TEXT,
    modele TEXT, statut TEXT, resultat TEXT, usage TEXT, cout_usd REAL, erreur TEXT,
    valide_le TEXT, valide_par TEXT, org TEXT);
CREATE TABLE IF NOT EXISTS ia_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, objet TEXT, entree INTEGER, sortie INTEGER, cout_usd REAL, org TEXT);
CREATE TABLE IF NOT EXISTS ia_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, friche_id TEXT, usage TEXT, created_at TEXT, note TEXT, modele TEXT, cout_usd REAL, org TEXT);
CREATE TABLE IF NOT EXISTS signaux (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cle TEXT UNIQUE, friche_id TEXT, type TEXT, niveau TEXT, titre TEXT,
    detail TEXT, source TEXT, url TEXT, date_evenement TEXT, detecte_le TEXT, vu INTEGER DEFAULT 0, org TEXT);
CREATE TABLE IF NOT EXISTS signaux_vus (org TEXT, signal_id INTEGER, PRIMARY KEY (org, signal_id));
CREATE TABLE IF NOT EXISTS organismes (id TEXT PRIMARY KEY, nom TEXT NOT NULL, cree_le TEXT, actif INTEGER DEFAULT 1,
    quota_ia INTEGER);
CREATE TABLE IF NOT EXISTS utilisateurs (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, nom TEXT,
    organisme_id TEXT, role TEXT NOT NULL, mdp TEXT, actif INTEGER DEFAULT 1, cree_le TEXT, derniere_connexion TEXT);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, utilisateur_id INTEGER, cree_le TEXT, expire_le TEXT);
CREATE TABLE IF NOT EXISTS reglages (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS maj_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, debut TEXT, fin TEXT, declencheur TEXT, etat TEXT, resume TEXT);
CREATE TABLE IF NOT EXISTS score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, friche_id TEXT, ts TEXT, usage TEXT,
    global REAL, rang INTEGER, rang_sur INTEGER, dims TEXT, motif TEXT, org TEXT);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn():
    with _lock:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


PRIVATE_TABLES = ("field_data", "ia_extractions", "ia_journal", "ia_notes", "score_history")


def _cols(c, table) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _migrate(c):
    """Bases créées avant l'arrivée des organismes : ajout des colonnes `org` (données existantes → « local »)."""
    if "org" not in _cols(c, "friches"):
        c.execute("ALTER TABLE friches ADD COLUMN org TEXT")
    for t in PRIVATE_TABLES:
        if "org" not in _cols(c, t):
            c.execute(f"ALTER TABLE {t} ADD COLUMN org TEXT")
            c.execute(f"UPDATE {t} SET org = 'local'")
    if "org" not in _cols(c, "signaux"):
        c.execute("ALTER TABLE signaux ADD COLUMN org TEXT")
    for t, key in (("portfolio", "friche_id"), ("parametres", "path")):
        if "org" not in _cols(c, t):
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            c.execute(f"ALTER TABLE {t} RENAME TO {t}_ancien")
            ddl = [x for x in SCHEMA.split(";") if f"CREATE TABLE IF NOT EXISTS {t} (" in x][0]
            c.execute(ddl)
            c.execute(f"INSERT INTO {t} ({', '.join(cols)}, org) SELECT {', '.join(cols)}, 'local' FROM {t}_ancien")
            c.execute(f"DROP TABLE {t}_ancien")
    c.execute("CREATE INDEX IF NOT EXISTS ix_friches_org ON friches(org)")


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)


# ------------------------------------------------------------------ portée des requêtes
COMMON_ORIGINS = ("cartofriches",)


def _org() -> str | None:
    return tenant.org()


def _visible(alias: str = "") -> tuple[str, list]:
    """Friches visibles : base commune + friches de l'organisme. Contexte système : toutes."""
    o = _org()
    a = f"{alias}." if alias else ""
    if o is None:
        return "1=1", []
    return f"({a}org IS NULL OR {a}org = ?)", [o]


def _mine(alias: str = "") -> tuple[str, list]:
    """Données privées de l'organisme courant (aucune en contexte système)."""
    a = f"{alias}." if alias else ""
    o = _org()
    return (f"{a}org = ?", [o]) if o is not None else (f"{a}org IS NULL AND 0", [])


def _need_org() -> str:
    o = _org()
    if o is None:
        raise PermissionError("opération réservée à un organisme")
    return o


def is_empty() -> bool:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM friches").fetchone()[0] == 0


def reset_all():
    with conn() as c:
        for t in ("communes", "friches", "field_data", "portfolio", "score_history"):
            c.execute(f"DELETE FROM {t}")


def delete_origin(origine: str):
    """Retire les friches d'une origine : celles de l'organisme courant ; en mode local ou système, toutes."""
    o = _org()
    with conn() as c:
        if o is None or (o == tenant.LOCAL and not tenant.auth_enabled()):
            c.execute("DELETE FROM friches WHERE origine = ?", (origine,))
        else:
            c.execute("DELETE FROM friches WHERE origine = ? AND org = ?", (origine, o))


# --------------------------------------------------------------- écriture
def upsert_commune(code: str, rec: Record):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO communes VALUES (?, ?)", (code, json.dumps(rec.to_dict(), ensure_ascii=False)))


def target_org(origine: str) -> str | None:
    """Organisme propriétaire d'une friche écrite : base commune pour les sources publiques partagées."""
    if origine in COMMON_ORIGINS or str(origine).startswith("demo"):
        return None
    o = _org()
    if o is None or (o == tenant.LOCAL and not tenant.auth_enabled() and origine == "pack-commun"):
        return None
    return o


def upsert_friche(rec: Record, origine: str, org: str | None | bool = False) -> str | None:
    """Écrit une friche. Retourne l'identifiant utilisé (None si refusée). Une friche privée dont l'identifiant est
    déjà pris par la base commune ou un autre organisme est enregistrée sous « identifiant@organisme »."""
    tgt = target_org(origine) if org is False else org
    fid = str(rec.get("id"))
    with conn() as c:
        ex = c.execute("SELECT org FROM friches WHERE id = ?", (fid,)).fetchone()
        if ex is not None and ex["org"] != tgt:
            if tgt is None:
                return None  # une source commune n'écrase jamais une friche privée
            if not (tgt == tenant.LOCAL and not tenant.auth_enabled()):
                fid = f"{fid}@{tgt}"
                rec.values["id"] = fid
        c.execute("INSERT OR REPLACE INTO friches (id, code_insee, departement, epci, origine, data, created_at, org) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (fid, rec.get("code_insee"), rec.get("departement"), rec.get("epci"), origine,
                   json.dumps(rec.to_dict(), ensure_ascii=False), now(), tgt))
    return fid


def bulk_load(communes: dict[str, Record], friches: list[Record], origine: str):
    with conn() as c:
        c.executemany("INSERT OR REPLACE INTO communes VALUES (?, ?)",
                      [(k, json.dumps(v.to_dict(), ensure_ascii=False)) for k, v in communes.items()])
        tgt = target_org(origine)
        c.executemany("INSERT OR REPLACE INTO friches (id, code_insee, departement, epci, origine, data, created_at, org) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      [(r.get("id"), r.get("code_insee"), r.get("departement"), r.get("epci"), origine,
                        json.dumps(r.to_dict(), ensure_ascii=False), now(), tgt) for r in friches])


# ----------------------------------------------------------------- lecture
def perimeters() -> dict:
    w, a = _visible()
    with conn() as c:
        deps = [dict(r) for r in c.execute(
            f"SELECT departement AS code, COUNT(*) AS n FROM friches WHERE {w} GROUP BY departement ORDER BY departement", a)]
        epcis = [dict(r) for r in c.execute(
            f"SELECT epci AS nom, departement, COUNT(*) AS n FROM friches WHERE {w} GROUP BY epci ORDER BY epci", a)]
        communes = [dict(r) for r in c.execute(
            "SELECT code_insee, json_extract(data, '$.values.commune') AS nom, departement, COUNT(*) AS n "
            f"FROM friches WHERE {w} GROUP BY code_insee ORDER BY nom", a)]
        origines = [dict(r) for r in c.execute(
            f"SELECT origine, COUNT(*) AS n, MAX(org IS NULL) AS commune FROM friches WHERE {w} GROUP BY origine", a)]
    return {"departements": deps, "epcis": epcis, "communes": communes, "origines": origines}


def _field_records(c, ids: list[str] | None = None) -> dict[str, Record]:
    w, a = _mine()
    q = f"SELECT * FROM field_data WHERE {w} ORDER BY created_at, id"
    out: dict[str, Record] = {}
    for r in c.execute(q, a):
        if ids is not None and r["friche_id"] not in ids:
            continue
        rec = out.setdefault(r["friche_id"], Record())
        rec.set(r["key"], json.loads(r["value"]),
                Provenance(r["source"] or "Donnée terrain", r["date"], r["confiance"] or "eleve",
                           f"saisie terrain{(' — ' + r['auteur']) if r['auteur'] else ''}"))
    return out


def load_sites(perimeter: dict | None = None) -> dict[str, Record]:
    """Charge les friches d'un périmètre, fusionnées avec les indicateurs
    communaux (en dessous) et les données terrain (au-dessus)."""
    perimeter = perimeter or {}
    typ = perimeter.get("type", "tout")
    vals = perimeter.get("values") or []
    w, wa = _visible()
    sql = f"SELECT * FROM friches WHERE {w}"
    args: list = list(wa)
    col = {"departement": "departement", "epci": "epci", "communes": "code_insee", "ids": "id"}.get(typ)
    if col and vals:
        sql += f" AND {col} IN ({','.join('?' * len(vals))})"
        args += list(vals)
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
        codes = {r["code_insee"] for r in rows}
        communes = {}
        for r in c.execute("SELECT * FROM communes"):
            if r["code_insee"] in codes:
                communes[r["code_insee"]] = Record.from_dict(json.loads(r["data"]))
        field = _field_records(c, [r["id"] for r in rows])
    out = {}
    for r in rows:
        f = Record.from_dict(json.loads(r["data"]))
        base = communes.get(r["code_insee"])
        if base is not None:
            real = not str(r["origine"] or "").startswith("demo")
            # Une friche réelle n'hérite jamais d'indicateurs communaux de démonstration
            keep = {k: v for k, v in base.values.items() if not k.startswith("_")
                    and not (real and k in base.prov and base.prov[k].source.endswith("(démo)"))}
            merged = Record(values=keep, prov={k: p for k, p in base.prov.items() if k in keep})
            merged = merged.merged_with(f)
        else:
            merged = f
        if r["id"] in field:
            merged = merged.merged_with(field[r["id"]])
        _derive_distance(merged)
        if isinstance(merged.values.get("commune"), str):
            from .ingest.mapping import nom_commune
            merged.values["commune"] = nom_commune(merged.values["commune"])
        merged.values["_origine"] = r["origine"]
        out[r["id"]] = merged
    return out


def _derive_distance(rec: Record) -> None:
    """Distance à la mairie calculée si la friche ne la renseigne pas et que la commune la connaît."""
    if rec.has("distance_centre_km") or not (rec.has("lat") and rec.has("lon") and rec.has("centre_lat") and rec.has("centre_lon")):
        return
    from .ingest.territoire import distance_km
    from .scoring.record import Provenance
    d = distance_km(rec.get("lat"), rec.get("lon"), rec.get("centre_lat"), rec.get("centre_lon"))
    p = rec.prov.get("centre_lat")
    rec.set("distance_centre_km", round(d, 2), Provenance("Calcul : distance à vol d'oiseau à la mairie (API Géo)",
                                                          p.date if p else None, "eleve", "coordonnées friche × mairie"))


def update_friche_epci(code_insee: str, epci: str):
    """Renseigne l'EPCI des friches d'une commune qui ne l'ont pas (Cartofriches ne le fournit pas)."""
    with conn() as c:
        rows = c.execute("SELECT id, data FROM friches WHERE code_insee = ? AND (epci IS NULL OR epci = '')", (code_insee,)).fetchall()
        for r in rows:
            d = json.loads(r["data"])
            d["values"]["epci"] = epci
            c.execute("UPDATE friches SET epci = ?, data = ? WHERE id = ?", (epci, json.dumps(d, ensure_ascii=False), r["id"]))


def update_friche_values(fid: str, values: dict) -> None:
    """Ajoute/écrase des indicateurs calculés sur une friche ({clé: (valeur, Provenance)}), origine conservée."""
    with conn() as c:
        r = c.execute("SELECT data FROM friches WHERE id = ?", (fid,)).fetchone()
        if r is None:
            return
        d = json.loads(r["data"])
        for k, (v, p) in values.items():
            d["values"][k] = v
            d.setdefault("prov", {})[k] = p.to_dict()
        c.execute("UPDATE friches SET data = ? WHERE id = ?", (json.dumps(d, ensure_ascii=False), fid))


def real_friche_ids() -> list[str]:
    w, a = _visible()
    with conn() as c:
        return [r[0] for r in c.execute(f"SELECT id FROM friches WHERE origine NOT LIKE 'demo%' AND {w}", a)]


def real_friche_communes() -> list[str]:
    w, a = _visible()
    with conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT code_insee FROM friches WHERE origine NOT LIKE 'demo%' "
                                        f"AND code_insee IS NOT NULL AND {w}", a)]


def get_commune(code: str) -> Record | None:
    with conn() as c:
        r = c.execute("SELECT data FROM communes WHERE code_insee = ?", (code,)).fetchone()
    return Record.from_dict(json.loads(r["data"])) if r else None


# -------------------------------------------------------------- terrain
def add_field(fid: str, key: str, value, source: str, date: str, confiance: str, auteur: str | None):
    with conn() as c:
        c.execute("INSERT INTO field_data (friche_id, key, value, source, date, confiance, auteur, created_at, org) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (fid, key, json.dumps(value, ensure_ascii=False), source, date, confiance,
                                                 auteur, now(), _need_org()))


def list_field(fid: str) -> list[dict]:
    with conn() as c:
        return [{**dict(r), "value": json.loads(r["value"])} for r in
                c.execute("SELECT * FROM field_data WHERE friche_id = ? AND org = ? ORDER BY created_at, id", (fid, _org()))]


def delete_field(fid: str, row_id: int):
    with conn() as c:
        c.execute("DELETE FROM field_data WHERE friche_id = ? AND id = ? AND org = ?", (fid, row_id, _org()))


# ------------------------------------------------------------- portfolio
def portfolio() -> dict[str, dict]:
    with conn() as c:
        return {r["friche_id"]: dict(r) for r in c.execute("SELECT * FROM portfolio WHERE org = ?", (_org(),))}


def set_portfolio(fid: str, stage: str, usage: str | None, notes: str | None):
    with conn() as c:
        c.execute("INSERT INTO portfolio (friche_id, stage, usage, notes, updated_at, org) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(org, friche_id) DO UPDATE SET "
                  "stage=excluded.stage, usage=COALESCE(excluded.usage, portfolio.usage), "
                  "notes=COALESCE(excluded.notes, portfolio.notes), updated_at=excluded.updated_at",
                  (fid, stage, usage, notes, now(), _need_org()))


def remove_portfolio(fid: str):
    with conn() as c:
        c.execute("DELETE FROM portfolio WHERE friche_id = ? AND org = ?", (fid, _org()))


def add_history(fid: str, usage: str, g: float, rang: int, rang_sur: int, dims: dict, motif: str):
    with conn() as c:
        c.execute("INSERT INTO score_history (friche_id, ts, usage, global, rang, rang_sur, dims, motif, org) VALUES (?,?,?,?,?,?,?,?,?)",
                  (fid, now(), usage, g, rang, rang_sur, json.dumps(dims), motif, _need_org()))


def history(fid: str) -> list[dict]:
    with conn() as c:
        return [{**dict(r), "dims": json.loads(r["dims"])} for r in
                c.execute("SELECT * FROM score_history WHERE friche_id = ? AND org = ? ORDER BY ts, id", (fid, _org()))]


# ------------------------------------------------------------- paramètres
def param_overrides() -> dict[str, dict]:
    with conn() as c:
        return {r["path"]: {"value": json.loads(r["value"]), "note": r["note"], "updated_at": r["updated_at"]}
                for r in c.execute("SELECT * FROM parametres WHERE org = ?", (_org() or "",))}


def set_param(path: str, value, note: str | None):
    with conn() as c:
        c.execute("INSERT INTO parametres (path, value, note, updated_at, org) VALUES (?,?,?,?,?) ON CONFLICT(org, path) "
                  "DO UPDATE SET value=excluded.value, note=excluded.note, updated_at=excluded.updated_at",
                  (path, json.dumps(value), note, now(), _need_org()))


def delete_param(path: str | None = None):
    with conn() as c:
        if path is None:
            c.execute("DELETE FROM parametres WHERE org = ?", (_org(),))
        else:
            c.execute("DELETE FROM parametres WHERE path = ? AND org = ?", (path, _org()))


# --------------------------------------------------------- extractions IA
def ia_create(eid: str, fid: str, fichier: str, sha: str, taille: int, modele: str):
    with conn() as c:
        c.execute("INSERT INTO ia_extractions (id, friche_id, fichier, sha256, taille, created_at, modele, statut, org) "
                  "VALUES (?,?,?,?,?,?,?, 'en_cours', ?)", (eid, fid, fichier, sha, taille, now(), modele, _need_org()))


def ia_update(eid: str, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in fields.values()]
    with conn() as c:
        c.execute(f"UPDATE ia_extractions SET {cols} WHERE id = ?", (*vals, eid))


def _ia_row(r) -> dict:
    d = dict(r)
    for k in ("resultat", "usage"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def ia_list(fid: str) -> list[dict]:
    with conn() as c:
        return [_ia_row(r) for r in c.execute("SELECT * FROM ia_extractions WHERE friche_id = ? AND org = ? "
                                              "ORDER BY created_at DESC", (fid, _org()))]


def ia_get(eid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM ia_extractions WHERE id = ? AND org = ?", (eid, _org())).fetchone()
    return _ia_row(r) if r else None


def ia_month_stats() -> dict:
    month = now()[:7]
    with conn() as c:
        r = c.execute("SELECT COUNT(*), COALESCE(SUM(cout_usd), 0) FROM ia_extractions WHERE substr(created_at, 1, 7) = ? "
                      "AND statut != 'erreur' AND org = ?", (month, _org())).fetchone()
        j = c.execute("SELECT COUNT(*), COALESCE(SUM(cout_usd), 0) FROM ia_journal WHERE substr(ts, 1, 7) = ? AND org = ?",
                      (month, _org())).fetchone()
    return {"mois": month, "documents": r[0], "autres_appels": j[0], "cout_usd": round(r[1] + j[1], 2)}


def ia_log(kind: str, objet: str, usage: dict, cout: float):
    with conn() as c:
        c.execute("INSERT INTO ia_journal (ts, type, objet, entree, sortie, cout_usd, org) VALUES (?,?,?,?,?,?,?)",
                  (now(), kind, objet[:200], usage.get("entree", 0), usage.get("sortie", 0), cout, _need_org()))


def ia_note_save(fid: str, usage: str, note: dict, modele: str, cout: float):
    with conn() as c:
        c.execute("INSERT INTO ia_notes (friche_id, usage, created_at, note, modele, cout_usd, org) VALUES (?,?,?,?,?,?,?)",
                  (fid, usage, now(), json.dumps(note, ensure_ascii=False), modele, cout, _need_org()))


def ia_note_latest(fid: str, usage: str | None = None) -> dict | None:
    q = "SELECT * FROM ia_notes WHERE friche_id = ? AND org = ?" + (" AND usage = ?" if usage else "") + \
        " ORDER BY created_at DESC, id DESC LIMIT 1"
    with conn() as c:
        r = c.execute(q, (fid, _org(), usage) if usage else (fid, _org())).fetchone()
    if not r:
        return None
    d = dict(r)
    d["note"] = json.loads(d["note"])
    return d


# -------------------------------------------------------------- réglages et mises à jour
def setting_get(cle: str, default=None):
    with conn() as c:
        r = c.execute("SELECT valeur FROM reglages WHERE cle = ?", (cle,)).fetchone()
    return json.loads(r["valeur"]) if r else default


def setting_set(cle: str, valeur) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO reglages VALUES (?, ?, ?)", (cle, json.dumps(valeur, ensure_ascii=False), now()))


def maj_start(declencheur: str) -> int:
    with conn() as c:
        return c.execute("INSERT INTO maj_journal (debut, declencheur, etat) VALUES (?, ?, 'en_cours')",
                         (now(), declencheur)).lastrowid


def maj_end(mid: int, etat: str, resume: dict) -> None:
    with conn() as c:
        c.execute("UPDATE maj_journal SET fin = ?, etat = ?, resume = ? WHERE id = ?",
                  (now(), etat, json.dumps(resume, ensure_ascii=False), mid))


def maj_list(limit: int = 15) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM maj_journal ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "resume": json.loads(r["resume"]) if r["resume"] else None} for r in rows]


def friches_rows(departements: list[str]) -> list[dict]:
    """Friches réelles (hors démonstration) des départements donnés, telles que stockées."""
    q = ",".join("?" * len(departements))
    with conn() as c:
        rows = c.execute(f"SELECT id, origine, data FROM friches WHERE origine NOT LIKE 'demo%' AND org IS NULL "
                         f"AND departement IN ({q})", departements).fetchall()
    return [{"id": r["id"], "origine": r["origine"], "data": json.loads(r["data"])} for r in rows]


def communes_rows(codes: list[str]) -> list[dict]:
    if not codes:
        return []
    out = []
    with conn() as c:
        for i in range(0, len(codes), 500):
            part = codes[i:i + 500]
            q = ",".join("?" * len(part))
            out += [{"code": r["code_insee"], "data": json.loads(r["data"])}
                    for r in c.execute(f"SELECT code_insee, data FROM communes WHERE code_insee IN ({q})", part)]
    return out


def friche_origins() -> dict[str, str]:
    w, a = _visible()
    with conn() as c:
        return {r[0]: r[1] for r in c.execute(f"SELECT id, origine FROM friches WHERE {w}", a)}


# -------------------------------------------------------------- signaux d'opportunité
def signal_add(sig: dict) -> bool:
    """Enregistre un signal ; False s'il était déjà connu (même clé)."""
    with conn() as c:
        o = sig["org"] if "org" in sig else _org()
        cle = sig["cle"] if o is None else f"{o}|{sig['cle']}"
        cur = c.execute("INSERT OR IGNORE INTO signaux (cle, friche_id, type, niveau, titre, detail, source, url, "
                        "date_evenement, detecte_le, org) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (cle, sig["friche_id"], sig["type"], sig["niveau"], sig["titre"], sig.get("detail"),
                         sig.get("source"), sig.get("url"), sig.get("date_evenement"), now(), o))
        return cur.rowcount > 0


def _signals_sql() -> tuple[str, list]:
    """Signaux visibles : publics ou de l'organisme, sur une friche visible ; « vu » est propre à l'organisme."""
    o = _org() or ""
    w, a = _visible("f")
    return (f"SELECT s.*, (v.signal_id IS NOT NULL) AS vu FROM signaux s JOIN friches f ON f.id = s.friche_id "
            f"LEFT JOIN signaux_vus v ON v.signal_id = s.id AND v.org = ? "
            f"WHERE (s.org IS NULL OR s.org = ?) AND {w}", [o, o, *a])


def signals_list(fid: str | None = None, non_vus: bool = False, limit: int = 500) -> list[dict]:
    sql, args = _signals_sql()
    if fid:
        sql += " AND s.friche_id = ?"
        args.append(fid)
    if non_vus:
        sql += " AND v.signal_id IS NULL"
    sql += " ORDER BY COALESCE(s.date_evenement, s.detecte_le) DESC, s.id DESC LIMIT ?"
    with conn() as c:
        return [dict(r) for r in c.execute(sql, (*args, limit))]


def signals_mark(ids: list[int] | None, vu: bool = True) -> None:
    o = _org() or ""
    with conn() as c:
        if ids is None:
            ids = [r["id"] for r in c.execute(*_signals_sql())]
        if vu:
            c.executemany("INSERT OR IGNORE INTO signaux_vus VALUES (?, ?)", [(o, i) for i in ids])
        else:
            c.executemany("DELETE FROM signaux_vus WHERE org = ? AND signal_id = ?", [(o, i) for i in ids])


def signal_counts() -> dict[str, dict]:
    sql, args = _signals_sql()
    with conn() as c:
        return {r[0]: {"total": r[1], "non_vus": r[2], "opportunites": r[3]} for r in c.execute(
            f"SELECT friche_id, COUNT(*), SUM(vu = 0), SUM(niveau = 'opportunite' AND vu = 0) FROM ({sql}) GROUP BY friche_id", args)}


# -------------------------------------------------------------- organismes, utilisateurs, sessions
def org_list() -> list[dict]:
    with conn() as c:
        return [dict(r) | {"utilisateurs": r["n"]} for r in c.execute(
            "SELECT o.*, (SELECT COUNT(*) FROM utilisateurs u WHERE u.organisme_id = o.id) AS n FROM organismes o ORDER BY nom")]


def org_get(oid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM organismes WHERE id = ?", (oid,)).fetchone()
    return dict(r) if r else None


def org_create(oid: str, nom: str, quota_ia: int | None = None) -> None:
    with conn() as c:
        c.execute("INSERT INTO organismes (id, nom, cree_le, actif, quota_ia) VALUES (?, ?, ?, 1, ?)", (oid, nom, now(), quota_ia))


def org_update(oid: str, **f) -> None:
    if not f:
        return
    with conn() as c:
        c.execute(f"UPDATE organismes SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), oid))


def user_by_email(email: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM utilisateurs WHERE lower(email) = lower(?)", (email.strip(),)).fetchone()
    return dict(r) if r else None


def user_get(uid: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM utilisateurs WHERE id = ?", (uid,)).fetchone()
    return dict(r) if r else None


def user_list(organisme_id: str | None = None) -> list[dict]:
    with conn() as c:
        rows = (c.execute("SELECT * FROM utilisateurs WHERE organisme_id = ? ORDER BY email", (organisme_id,)) if organisme_id
                else c.execute("SELECT * FROM utilisateurs ORDER BY organisme_id, email"))
        return [{k: v for k, v in dict(r).items() if k != "mdp"} for r in rows]


def user_create(email: str, nom: str | None, organisme_id: str | None, role: str, mdp_hash: str) -> int:
    with conn() as c:
        return c.execute("INSERT INTO utilisateurs (email, nom, organisme_id, role, mdp, actif, cree_le) VALUES (?,?,?,?,?,1,?)",
                         (email.strip().lower(), nom, organisme_id, role, mdp_hash, now())).lastrowid


def user_update(uid: int, **f) -> None:
    if not f:
        return
    with conn() as c:
        c.execute(f"UPDATE utilisateurs SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), uid))
        if f.get("actif") == 0 or "mdp" in f:
            c.execute("DELETE FROM sessions WHERE utilisateur_id = ?", (uid,))


def session_create(token_hash: str, uid: int, expire: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (token_hash, uid, now(), expire))
        c.execute("UPDATE utilisateurs SET derniere_connexion = ? WHERE id = ?", (now(), uid))
        c.execute("DELETE FROM sessions WHERE expire_le < ?", (now(),))


def session_user(token_hash: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT u.* FROM sessions s JOIN utilisateurs u ON u.id = s.utilisateur_id "
                      "LEFT JOIN organismes o ON o.id = u.organisme_id "
                      "WHERE s.token_hash = ? AND s.expire_le > ? AND u.actif = 1 AND (u.organisme_id IS NULL OR o.actif = 1)",
                      (token_hash, now())).fetchone()
    return {k: v for k, v in dict(r).items() if k != "mdp"} if r else None


def session_delete(token_hash: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
