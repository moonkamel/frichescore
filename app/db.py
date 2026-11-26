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
CREATE TABLE IF NOT EXISTS ia_extractions (
    id TEXT PRIMARY KEY, friche_id TEXT, fichier TEXT, sha256 TEXT, taille INTEGER, created_at TEXT,
    modele TEXT, statut TEXT, resultat TEXT, usage TEXT, cout_usd REAL, erreur TEXT,
    valide_le TEXT, valide_par TEXT);
CREATE TABLE IF NOT EXISTS ia_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, objet TEXT, entree INTEGER, sortie INTEGER, cout_usd REAL);
CREATE TABLE IF NOT EXISTS ia_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, friche_id TEXT, usage TEXT, created_at TEXT, note TEXT, modele TEXT, cout_usd REAL);
CREATE TABLE IF NOT EXISTS signaux (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cle TEXT UNIQUE, friche_id TEXT, type TEXT, niveau TEXT, titre TEXT,
    detail TEXT, source TEXT, url TEXT, date_evenement TEXT, detecte_le TEXT, vu INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reglages (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS maj_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, debut TEXT, fin TEXT, declencheur TEXT, etat TEXT, resume TEXT);
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
    with conn() as c:
        return [r[0] for r in c.execute("SELECT id FROM friches WHERE origine NOT LIKE 'demo%'")]


def real_friche_communes() -> list[str]:
    with conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT code_insee FROM friches WHERE origine NOT LIKE 'demo%' AND code_insee IS NOT NULL")]


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


# --------------------------------------------------------- extractions IA
def ia_create(eid: str, fid: str, fichier: str, sha: str, taille: int, modele: str):
    with conn() as c:
        c.execute("INSERT INTO ia_extractions (id, friche_id, fichier, sha256, taille, created_at, modele, statut) "
                  "VALUES (?,?,?,?,?,?,?, 'en_cours')", (eid, fid, fichier, sha, taille, now(), modele))


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
        return [_ia_row(r) for r in c.execute("SELECT * FROM ia_extractions WHERE friche_id = ? ORDER BY created_at DESC", (fid,))]


def ia_get(eid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM ia_extractions WHERE id = ?", (eid,)).fetchone()
    return _ia_row(r) if r else None


def ia_month_stats() -> dict:
    month = now()[:7]
    with conn() as c:
        r = c.execute("SELECT COUNT(*), COALESCE(SUM(cout_usd), 0) FROM ia_extractions WHERE substr(created_at, 1, 7) = ? "
                      "AND statut != 'erreur'", (month,)).fetchone()
        j = c.execute("SELECT COUNT(*), COALESCE(SUM(cout_usd), 0) FROM ia_journal WHERE substr(ts, 1, 7) = ?", (month,)).fetchone()
    return {"mois": month, "documents": r[0], "autres_appels": j[0], "cout_usd": round(r[1] + j[1], 2)}


def ia_log(kind: str, objet: str, usage: dict, cout: float):
    with conn() as c:
        c.execute("INSERT INTO ia_journal (ts, type, objet, entree, sortie, cout_usd) VALUES (?,?,?,?,?,?)",
                  (now(), kind, objet[:200], usage.get("entree", 0), usage.get("sortie", 0), cout))


def ia_note_save(fid: str, usage: str, note: dict, modele: str, cout: float):
    with conn() as c:
        c.execute("INSERT INTO ia_notes (friche_id, usage, created_at, note, modele, cout_usd) VALUES (?,?,?,?,?,?)",
                  (fid, usage, now(), json.dumps(note, ensure_ascii=False), modele, cout))


def ia_note_latest(fid: str, usage: str | None = None) -> dict | None:
    q = "SELECT * FROM ia_notes WHERE friche_id = ?" + (" AND usage = ?" if usage else "") + " ORDER BY created_at DESC, id DESC LIMIT 1"
    with conn() as c:
        r = c.execute(q, (fid, usage) if usage else (fid,)).fetchone()
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
        rows = c.execute(f"SELECT id, origine, data FROM friches WHERE origine NOT LIKE 'demo%' AND departement IN ({q})",
                         departements).fetchall()
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
    with conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT id, origine FROM friches")}


# -------------------------------------------------------------- signaux d'opportunité
def signal_add(sig: dict) -> bool:
    """Enregistre un signal ; False s'il était déjà connu (même clé)."""
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO signaux (cle, friche_id, type, niveau, titre, detail, source, url, "
                        "date_evenement, detecte_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (sig["cle"], sig["friche_id"], sig["type"], sig["niveau"], sig["titre"], sig.get("detail"),
                         sig.get("source"), sig.get("url"), sig.get("date_evenement"), now()))
        return cur.rowcount > 0


def signals_list(fid: str | None = None, non_vus: bool = False, limit: int = 500) -> list[dict]:
    sql, args = "SELECT * FROM signaux WHERE 1=1", []
    if fid:
        sql += " AND friche_id = ?"
        args.append(fid)
    if non_vus:
        sql += " AND vu = 0"
    sql += " ORDER BY COALESCE(date_evenement, detecte_le) DESC, id DESC LIMIT ?"
    with conn() as c:
        return [dict(r) for r in c.execute(sql, (*args, limit))]


def signals_mark(ids: list[int] | None, vu: bool = True) -> None:
    with conn() as c:
        if ids is None:
            c.execute("UPDATE signaux SET vu = ?", (int(vu),))
        else:
            c.executemany("UPDATE signaux SET vu = ? WHERE id = ?", [(int(vu), i) for i in ids])


def signal_counts() -> dict[str, dict]:
    with conn() as c:
        return {r[0]: {"total": r[1], "non_vus": r[2], "opportunites": r[3]} for r in c.execute(
            "SELECT friche_id, COUNT(*), SUM(vu = 0), SUM(niveau = 'opportunite' AND vu = 0) FROM signaux GROUP BY friche_id")}
