"""Persistance locale SQLite — aucune dépendance à un service cloud."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .scoring.record import Provenance, Record

DB_PATH = Path(os.environ.get("FRICHESCORE_DB", Path(__file__).resolve().parent.parent / "data" / "frichescore.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS communes (code_insee TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS friches (
    id TEXT PRIMARY KEY, code_insee TEXT, departement TEXT, epci TEXT, origine TEXT,
    data TEXT NOT NULL, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_friches_dep ON friches(departement);
CREATE INDEX IF NOT EXISTS ix_friches_epci ON friches(epci);
CREATE TABLE IF NOT EXISTS field_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT, friche_id TEXT, key TEXT, value TEXT,
    source TEXT, date TEXT, confiance TEXT, auteur TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS portfolio (
    friche_id TEXT PRIMARY KEY, stage TEXT, usage TEXT, notes TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS parametres (
    path TEXT PRIMARY KEY, value TEXT NOT NULL, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, friche_id TEXT, ts TEXT, usage TEXT,
    global REAL, rang INTEGER, rang_sur INTEGER, dims TEXT, motif TEXT);
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


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def is_empty() -> bool:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM friches").fetchone()[0] == 0


def reset_all():
    with conn() as c:
        for t in ("communes", "friches", "field_data", "portfolio", "score_history"):
            c.execute(f"DELETE FROM {t}")


def delete_origin(origine: str):
    with conn() as c:
        c.execute("DELETE FROM friches WHERE origine = ?", (origine,))


# --------------------------------------------------------------- écriture
def upsert_commune(code: str, rec: Record):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO communes VALUES (?, ?)", (code, json.dumps(rec.to_dict(), ensure_ascii=False)))


def upsert_friche(rec: Record, origine: str):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO friches VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (rec.get("id"), rec.get("code_insee"), rec.get("departement"), rec.get("epci"), origine,
                   json.dumps(rec.to_dict(), ensure_ascii=False), now()))


def bulk_load(communes: dict[str, Record], friches: list[Record], origine: str):
    with conn() as c:
        c.executemany("INSERT OR REPLACE INTO communes VALUES (?, ?)",
                      [(k, json.dumps(v.to_dict(), ensure_ascii=False)) for k, v in communes.items()])
        c.executemany("INSERT OR REPLACE INTO friches VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(r.get("id"), r.get("code_insee"), r.get("departement"), r.get("epci"), origine,
                        json.dumps(r.to_dict(), ensure_ascii=False), now()) for r in friches])


# ----------------------------------------------------------------- lecture
def perimeters() -> dict:
    with conn() as c:
        deps = [dict(r) for r in c.execute(
            "SELECT departement AS code, COUNT(*) AS n FROM friches GROUP BY departement ORDER BY departement")]
        epcis = [dict(r) for r in c.execute(
            "SELECT epci AS nom, departement, COUNT(*) AS n FROM friches GROUP BY epci ORDER BY epci")]
        communes = [dict(r) for r in c.execute(
            "SELECT code_insee, json_extract(data, '$.values.commune') AS nom, departement, COUNT(*) AS n "
            "FROM friches GROUP BY code_insee ORDER BY nom")]
        origines = [dict(r) for r in c.execute("SELECT origine, COUNT(*) AS n FROM friches GROUP BY origine")]
    return {"departements": deps, "epcis": epcis, "communes": communes, "origines": origines}


def _field_records(c, ids: list[str] | None = None) -> dict[str, Record]:
    q = "SELECT * FROM field_data ORDER BY created_at, id"
    out: dict[str, Record] = {}
    for r in c.execute(q):
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
    sql = "SELECT * FROM friches"
    args: list = []
    if typ == "departement" and vals:
        sql += f" WHERE departement IN ({','.join('?' * len(vals))})"
        args = vals
    elif typ == "epci" and vals:
        sql += f" WHERE epci IN ({','.join('?' * len(vals))})"
        args = vals
    elif typ == "communes" and vals:
        sql += f" WHERE code_insee IN ({','.join('?' * len(vals))})"
        args = vals
    elif typ == "ids" and vals:
        sql += f" WHERE id IN ({','.join('?' * len(vals))})"
        args = vals
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
            merged = Record(values={k: v for k, v in base.values.items() if not k.startswith("_")}, prov=dict(base.prov))
            merged = merged.merged_with(f)
        else:
            merged = f
        if r["id"] in field:
            merged = merged.merged_with(field[r["id"]])
        merged.values["_origine"] = r["origine"]
        out[r["id"]] = merged
    return out


def get_commune(code: str) -> Record | None:
    with conn() as c:
        r = c.execute("SELECT data FROM communes WHERE code_insee = ?", (code,)).fetchone()
    return Record.from_dict(json.loads(r["data"])) if r else None


# -------------------------------------------------------------- terrain
def add_field(fid: str, key: str, value, source: str, date: str, confiance: str, auteur: str | None):
    with conn() as c:
        c.execute("INSERT INTO field_data (friche_id, key, value, source, date, confiance, auteur, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?)", (fid, key, json.dumps(value, ensure_ascii=False), source, date, confiance, auteur, now()))


def list_field(fid: str) -> list[dict]:
    with conn() as c:
        return [{**dict(r), "value": json.loads(r["value"])} for r in
                c.execute("SELECT * FROM field_data WHERE friche_id = ? ORDER BY created_at, id", (fid,))]


def delete_field(fid: str, row_id: int):
    with conn() as c:
        c.execute("DELETE FROM field_data WHERE friche_id = ? AND id = ?", (fid, row_id))


# ------------------------------------------------------------- portfolio
def portfolio() -> dict[str, dict]:
    with conn() as c:
        return {r["friche_id"]: dict(r) for r in c.execute("SELECT * FROM portfolio")}


def set_portfolio(fid: str, stage: str, usage: str | None, notes: str | None):
    with conn() as c:
        c.execute("INSERT INTO portfolio VALUES (?,?,?,?,?) ON CONFLICT(friche_id) DO UPDATE SET "
                  "stage=excluded.stage, usage=COALESCE(excluded.usage, portfolio.usage), "
                  "notes=COALESCE(excluded.notes, portfolio.notes), updated_at=excluded.updated_at",
                  (fid, stage, usage, notes, now()))


def remove_portfolio(fid: str):
    with conn() as c:
        c.execute("DELETE FROM portfolio WHERE friche_id = ?", (fid,))


def add_history(fid: str, usage: str, g: float, rang: int, rang_sur: int, dims: dict, motif: str):
    with conn() as c:
        c.execute("INSERT INTO score_history (friche_id, ts, usage, global, rang, rang_sur, dims, motif) VALUES (?,?,?,?,?,?,?,?)",
                  (fid, now(), usage, g, rang, rang_sur, json.dumps(dims), motif))


def history(fid: str) -> list[dict]:
    with conn() as c:
        return [{**dict(r), "dims": json.loads(r["dims"])} for r in
                c.execute("SELECT * FROM score_history WHERE friche_id = ? ORDER BY ts, id", (fid,))]


# ------------------------------------------------------------- paramètres
def param_overrides() -> dict[str, dict]:
    with conn() as c:
        return {r["path"]: {"value": json.loads(r["value"]), "note": r["note"], "updated_at": r["updated_at"]}
                for r in c.execute("SELECT * FROM parametres")}


def set_param(path: str, value, note: str | None):
    with conn() as c:
        c.execute("INSERT INTO parametres VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET value=excluded.value, "
                  "note=excluded.note, updated_at=excluded.updated_at", (path, json.dumps(value), note, now()))


def delete_param(path: str | None = None):
    with conn() as c:
        if path is None:
            c.execute("DELETE FROM parametres")
        else:
            c.execute("DELETE FROM parametres WHERE path = ?", (path,))
