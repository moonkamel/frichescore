"""Mise à jour automatique (nocturne) et packs de données par département.

Deux façons de tenir la base à jour sans faire attendre les utilisateurs :
- mode « apis » : chaque nuit, le serveur réinterroge lui-même les API publiques (Cartofriches, Géorisques, DVF,
  indicateurs communaux et de site) pour les communes déjà suivies et les départements choisis ;
- mode « pack » : chaque nuit, il télécharge les packs départementaux publiés par un serveur FricheScore central
  (qui, lui, tourne en mode « apis » avec la publication des packs activée) et les importe en quelques secondes.

Un pack contient les friches réelles d'un ou plusieurs départements et les indicateurs des communes concernées.
Il ne contient jamais les données propres à un utilisateur : saisies terrain, portfolio, documents, notes IA.

Ligne de commande (serveur central, tâche planifiée du système) :
    python -m app.maj --departements 59 62 --publier
"""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import db, tenant
from .jobs import running, start_job
from .scoring.record import Record

LIBELLE = "Mise à jour"
DEP_RE = re.compile(r"^(\d{2}|2A|2B|97\d)$")
PACK_FORMAT = "frichescore-pack"
PACK_VERSION = 1
MAX_PACK_BYTES = 300 * 1024 * 1024

DEFAUT = {"actif": False, "heure": "03:00", "mode": "apis", "departements": [], "rafraichir_fichiers": True,
          "pack_url": "", "publier_packs": False, "signaux_bodacc": True}


# ------------------------------------------------------------------ réglages
def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("FRICHESCORE_FUSEAU", "Europe/Paris"))
    except Exception:  # noqa: BLE001 — base des fuseaux absente : heure locale du serveur
        return None


def maintenant() -> datetime:
    tz = _tz()
    return datetime.now(tz) if tz else datetime.now()


def norm_dep(d: str) -> str:
    d = str(d).strip().upper()
    return d.zfill(2) if d.isdigit() and len(d) == 1 else d


def reglages() -> dict:
    return {**DEFAUT, **(db.setting_get("maj_auto") or {})}


def enregistrer(data: dict) -> dict:
    r = reglages()
    erreurs = []
    if "actif" in data:
        r["actif"] = bool(data["actif"])
    if "heure" in data:
        if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(data["heure"])):
            r["heure"] = data["heure"]
        else:
            erreurs.append("heure attendue au format HH:MM (ex. 03:00)")
    if "mode" in data:
        if data["mode"] in ("apis", "pack"):
            r["mode"] = data["mode"]
        else:
            erreurs.append("mode attendu : apis ou pack")
    if "departements" in data:
        deps = [norm_dep(x) for x in (data["departements"] or []) if str(x).strip()]
        bad = [x for x in deps if not DEP_RE.match(x)]
        if bad:
            erreurs.append(f"codes de département invalides : {', '.join(bad)}")
        else:
            r["departements"] = sorted(set(deps))
    if "pack_url" in data:
        u = str(data["pack_url"] or "").strip().rstrip("/")
        if u and not u.startswith(("http://", "https://")):
            erreurs.append("l'adresse du serveur de packs doit commencer par http:// ou https://")
        else:
            r["pack_url"] = u
    for k in ("rafraichir_fichiers", "publier_packs", "signaux_bodacc"):
        if k in data:
            r[k] = bool(data[k])
    if r["mode"] == "pack" and r["actif"] and not (r["pack_url"] and r["departements"]):
        erreurs.append("en mode pack, indiquez l'adresse du serveur et au moins un département")
    if erreurs:
        raise ValueError(" ; ".join(erreurs))
    db.setting_set("maj_auto", r)
    return r


def prochaine_execution(r: dict, now: datetime | None = None) -> str | None:
    if not r.get("actif"):
        return None
    from datetime import timedelta
    now = now or maintenant()
    h, m = map(int, r["heure"].split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now or db.setting_get("maj_derniere_auto") == now.date().isoformat():
        t += timedelta(days=1)
    return t.isoformat(timespec="minutes")


def etat() -> dict:
    r = reglages()
    job = running(LIBELLE)
    return {"reglages": r, "prochaine": prochaine_execution(r), "en_cours": job["id"] if job else None,
            "journal": db.maj_list(), "packs_publies": packs_publies(), "fuseau": str(_tz() or "heure du serveur")}


# ------------------------------------------------------------------ instantané des scores
def _instantane() -> dict[str, dict]:
    from . import tenant
    with tenant.PARAMS.use(tenant.org()):
        return _instantane_()


def _instantane_() -> dict[str, dict]:
    from .service import _w
    from .scoring.stress import base_ranking
    sites = {k: v for k, v in db.load_sites({"type": "tout"}).items() if not str(v.get("_origine") or "").startswith("demo")}
    ranked = base_ranking(sites, None, _w(None, True), True)
    out = {sid: {"nom": sites[sid].get("nom"), "commune": sites[sid].get("commune"), "global": None} for sid in sites}
    for sid, x in ranked.items():
        out[sid].update(**{"global": round(x["g"]["global"], 1), "usage": x["usage"]})
    return out


def _ecarts(avant: dict, apres: dict, seuil: float = 2.0) -> dict:
    nouvelles = [{"id": k, "nom": v["nom"], "commune": v["commune"], "global": v["global"]} for k, v in apres.items() if k not in avant]
    modif = []
    for k, v in apres.items():
        a = avant.get(k)
        if a and a["global"] is not None and v["global"] is not None and abs(v["global"] - a["global"]) >= seuil:
            modif.append({"id": k, "nom": v["nom"], "commune": v["commune"], "avant": a["global"], "apres": v["global"],
                          "ecart": round(v["global"] - a["global"], 1)})
        elif a and (a["global"] is None) != (v["global"] is None):
            modif.append({"id": k, "nom": v["nom"], "commune": v["commune"], "avant": a["global"], "apres": v["global"],
                          "ecart": None})
    modif.sort(key=lambda x: -abs(x["ecart"] or 100))
    return {"nouvelles": nouvelles[:50], "nb_nouvelles": len(nouvelles), "scores_modifies": modif[:50],
            "nb_scores_modifies": len(modif)}


# ------------------------------------------------------------------ exécution
def executer(progress=None, declencheur: str = "manuel", reglages_: dict | None = None) -> dict:
    """Met la base à jour selon les réglages ; consigne le résultat dans le journal."""
    r = reglages_ or reglages()
    mid = db.maj_start(declencheur)
    t0 = time.time()
    try:
        from . import signaux as SG
        avant = _instantane()
        valeurs_avant = SG.valeurs(db.load_sites({"type": "tout"}))
        res = _maj_packs(r, progress) if r["mode"] == "pack" else _maj_apis(r, progress)
        sites_apres = db.load_sites({"type": "tout"})
        nouveaux = SG.enregistrer(SG.changements(valeurs_avant, SG.valeurs(sites_apres)))
        nouveaux += SG.absentes(res.get("absentes_de_cartofriches") or [])
        if r.get("signaux_bodacc", True):
            b = SG.scan_bodacc(sites_apres, progress)
            nouveaux += b["nouveaux"]
            res["bodacc"] = {k: b[k] for k in ("sirens", "annonces", "nouveaux")}
            res["erreurs"] = res.get("erreurs", []) + b["erreurs"]
            if tenant.org() is None:  # mise à jour nocturne : SIREN propriétaires saisis par chaque organisme
                for o in db.org_list():
                    if not o["actif"]:
                        continue
                    with tenant.as_org(o["id"]):
                        bp = SG.scan_bodacc(db.load_sites({"type": "tout"}), None, saisies_seulement=True)
                    nouveaux += bp["nouveaux"]
                    res["bodacc"]["sirens"] += bp["sirens"]
        res["signaux_nouveaux"] = nouveaux
        if r.get("publier_packs") and r["mode"] == "apis":
            if progress:
                progress("Publication des packs", 0, 1)
            res["packs_publies"] = publier_packs()
        res.update(_ecarts(avant, _instantane()))
        res["duree_s"] = round(time.time() - t0)
        res["erreurs"] = res.get("erreurs", [])[:40]
        etat_ = "termine_avec_erreurs" if res["erreurs"] else "termine"
        db.maj_end(mid, etat_, res)
        return res
    except Exception as e:  # noqa: BLE001
        db.maj_end(mid, "erreur", {"erreurs": [f"{type(e).__name__} : {e}"], "duree_s": round(time.time() - t0)})
        raise


def _maj_apis(r: dict, progress) -> dict:
    from . import service
    from .ingest import territoire
    erreurs: list[str] = []
    codes = set(_communes_cartofriches())
    for dep in r["departements"]:
        if progress:
            progress(f"Liste des communes du département {dep}", 0, 1)
        try:
            codes |= set(territoire.communes_du_departement(dep))
        except Exception as e:  # noqa: BLE001
            erreurs.append(f"département {dep} : liste des communes indisponible ({getattr(e, 'message', e)})")
    autres = [sid for sid, o in db.friche_origins().items() if not o.startswith("demo") and o != "cartofriches"]
    if not codes and not autres:
        return {"mode": "apis", "communes": 0, "importees": 0, "erreurs": erreurs,
                "message": "Rien à mettre à jour : aucune friche réelle ni département suivi."}
    avant = {sid: (rec.get("code_insee"), rec.get("nom")) for sid, rec in db.load_sites({"type": "tout"}).items()
             if rec.get("_origine") == "cartofriches"}
    rep = service.ingest_cartofriches(sorted(codes), True, True, progress, True, True, max_codes=None,
                                      refresh=bool(r.get("rafraichir_fichiers")))
    # friches Cartofriches qui n'apparaissent plus dans une commune interrogée avec succès (signalées, pas supprimées)
    ok = set(rep.get("communes_interrogees", []))
    vues = set(rep.get("ids_importes", []))
    absentes = [{"id": sid, "nom": nom} for sid, (ci, nom) in sorted(avant.items()) if ci in ok and sid not in vues]
    if autres:  # friches importées par CSV : indicateurs de site et communaux rafraîchis
        rs = service.enrich_sites(autres, True, progress)
        erreurs += rs.get("erreurs", [])
        sites_autres = db.load_sites({"type": "ids", "values": autres})
        oc = sorted({x.get("code_insee") for x in sites_autres.values()} - {None} - codes)
        if oc:
            rc = service.enrich_communes(oc, progress, bool(r.get("rafraichir_fichiers")))
            erreurs += rc.get("erreurs", [])
    return {"mode": "apis", "communes": len(codes), "communes_interrogees": len(ok), "importees": rep["importees"],
            "absentes_de_cartofriches": absentes[:50], "nb_absentes": len(absentes),
            "erreurs": erreurs + rep["erreurs"]}


def _communes_cartofriches() -> list[str]:
    """Communes portant au moins une friche Cartofriches (même si elles portent aussi des friches CSV)."""
    with db.conn() as c:
        return [row[0] for row in c.execute(
            "SELECT DISTINCT code_insee FROM friches WHERE origine = 'cartofriches' AND code_insee IS NOT NULL")]


def _maj_packs(r: dict, progress) -> dict:
    from .ingest import connectors as K
    erreurs, bilan = [], {}
    deps = r["departements"]
    with K._client(300) as c:
        for i, dep in enumerate(deps):
            if progress:
                progress(f"Pack du département {dep}", i, len(deps))
            url = f"{r['pack_url']}/api/packs/{dep}.json.gz"
            try:
                resp = c.get(url)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                if len(resp.content) > MAX_PACK_BYTES:
                    raise RuntimeError("pack trop volumineux")
                bilan[dep] = importer_pack(resp.content)
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"département {dep} : pack indisponible à {url} ({type(e).__name__} : {e})")
    return {"mode": "pack", "departements": deps, "packs": bilan,
            "importees": sum(b["friches_nouvelles"] + b["friches_mises_a_jour"] for b in bilan.values()), "erreurs": erreurs}


# ------------------------------------------------------------------ packs
def packs_dir() -> Path:
    d = db.DB_PATH.parent / "packs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def construire_pack(departements: list[str]) -> bytes:
    deps = sorted({norm_dep(d) for d in departements})
    bad = [d for d in deps if not DEP_RE.match(d)]
    if bad or not deps:
        raise ValueError(f"codes de département invalides : {', '.join(bad) or 'aucun'}")
    friches = db.friches_rows(deps)
    codes = sorted({f["data"].get("values", {}).get("code_insee") for f in friches} - {None})
    journal = [j for j in db.maj_list(5) if j["fin"]]
    payload = {"format": PACK_FORMAT, "version": PACK_VERSION, "cree_le": db.now(), "departements": deps,
               "derniere_mise_a_jour": journal[0]["fin"] if journal else None,
               "contenu": "friches réelles et indicateurs communaux ; aucune donnée utilisateur",
               "friches": friches, "communes": db.communes_rows(codes)}
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), compresslevel=6)


def lire_pack(raw: bytes) -> dict:
    if raw[:2] == b"\x1f\x8b":
        d = gzip.GzipFile(fileobj=__import__("io").BytesIO(raw))
        raw = d.read(MAX_PACK_BYTES + 1)
        if len(raw) > MAX_PACK_BYTES:
            raise ValueError("pack trop volumineux une fois décompressé")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"fichier illisible : ce n'est pas un pack FricheScore ({type(e).__name__})") from e
    if not isinstance(data, dict) or data.get("format") != PACK_FORMAT:
        raise ValueError("ce fichier n'est pas un pack FricheScore")
    if int(data.get("version") or 0) > PACK_VERSION:
        raise ValueError(f"pack de version {data.get('version')} : mettez FricheScore à jour pour le lire")
    return data


def importer_pack(raw: bytes, dry_run: bool = False) -> dict:
    """Importe un pack. Les friches d'une autre origine portant le même identifiant (ex. votre CSV) ne sont pas
    écrasées ; les indicateurs communaux du pack remplacent les valeurs existantes, indicateur par indicateur."""
    data = lire_pack(raw)
    origins = db.friche_origins()
    rep = {"departements": data.get("departements"), "cree_le": data.get("cree_le"),
           "derniere_mise_a_jour": data.get("derniere_mise_a_jour"), "friches_nouvelles": 0, "friches_mises_a_jour": 0,
           "friches_ignorees": [], "communes": 0, "simulation": dry_run}
    friches = []
    for f in data.get("friches") or []:
        if not isinstance(f, dict) or not f.get("id") or not isinstance(f.get("data"), dict):
            continue
        origine = str(f.get("origine") or "pack")
        if origine.startswith("demo"):
            continue
        ex = origins.get(f["id"])
        if ex and ex != origine:
            rep["friches_ignorees"].append(f"{f['id']} : existe déjà avec l'origine « {ex} »")
            continue
        rep["friches_mises_a_jour" if ex else "friches_nouvelles"] += 1
        friches.append((Record.from_dict(f["data"]), origine))
    communes = [(c["code"], Record.from_dict(c["data"])) for c in data.get("communes") or []
                if isinstance(c, dict) and c.get("code") and isinstance(c.get("data"), dict)]
    rep["communes"] = len(communes)
    rep["friches_ignorees"] = rep["friches_ignorees"][:30]
    if not dry_run:
        for rec, origine in friches:
            db.upsert_friche(rec, origine, org=None)  # un pack alimente la base commune
        for code, rec in communes:
            base = db.get_commune(code)
            db.upsert_commune(code, base.merged_with(rec) if base else rec)
    return rep


def publier_packs() -> list[dict]:
    """Écrit un pack par département présent dans la base (friches réelles) dans le dossier des packs."""
    with db.conn() as c:
        deps = [r[0] for r in c.execute("SELECT DISTINCT departement FROM friches WHERE origine NOT LIKE 'demo%' "
                                        "AND departement IS NOT NULL")]
    out = []
    for dep in sorted(d for d in deps if DEP_RE.match(str(d))):
        raw = construire_pack([dep])
        tmp = packs_dir() / f".{dep}.json.gz.tmp"
        tmp.write_bytes(raw)
        tmp.replace(packs_dir() / f"{dep}.json.gz")
        out.append({"departement": dep, "octets": len(raw)})
    return out


def packs_publies() -> list[dict]:
    d = db.DB_PATH.parent / "packs"
    if not d.exists():
        return []
    return [{"departement": p.name.split(".")[0], "octets": p.stat().st_size,
             "date": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes")}
            for p in sorted(d.glob("*.json.gz")) if not p.name.startswith(".")]


# ------------------------------------------------------------------ planificateur
def doit_lancer(r: dict, now: datetime, derniere: str | None) -> bool:
    if not r.get("actif"):
        return False
    h, m = map(int, r["heure"].split(":"))
    return (now.hour, now.minute) >= (h, m) and derniere != now.date().isoformat()


def lancer(declencheur: str = "manuel") -> dict:
    job = running(LIBELLE)
    if job:
        return job
    r = reglages()
    return start_job(lambda p: executer(p, declencheur, r), f"{LIBELLE} {'automatique' if declencheur == 'nuit' else 'manuelle'}")


_started = False


def demarrer_planificateur(intervalle: float = 30.0) -> bool:
    global _started
    if _started or os.environ.get("FRICHESCORE_PLANIFICATEUR", "1") == "0":
        return False
    _started = True

    def boucle():
        while True:
            try:
                now = maintenant()
                if doit_lancer(reglages(), now, db.setting_get("maj_derniere_auto")) and not running(LIBELLE):
                    db.setting_set("maj_derniere_auto", now.date().isoformat())
                    lancer("nuit")
            except Exception:  # noqa: BLE001 — le planificateur ne doit jamais s'arrêter
                pass
            time.sleep(intervalle)

    threading.Thread(target=boucle, daemon=True, name="planificateur-maj").start()
    return True


# ------------------------------------------------------------------ ligne de commande
def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Mise à jour FricheScore depuis les API publiques, et publication des packs.")
    ap.add_argument("--departements", nargs="*", default=None, help="départements à couvrir entièrement (ex. 59 62)")
    ap.add_argument("--publier", action="store_true", help="écrire un pack par département dans le dossier des packs")
    ap.add_argument("--sortie", help="écrire aussi un pack unique des départements donnés dans ce fichier")
    a = ap.parse_args(argv)
    db.init()
    r = reglages()
    r = {**r, "mode": "apis", "publier_packs": a.publier}
    if a.departements is not None:
        r["departements"] = sorted({norm_dep(d) for d in a.departements})

    def prog(etape, fait, total):
        print(f"\r{etape} {fait}/{total}".ljust(90), end="", flush=True)
    res = executer(prog, "ligne de commande", r)
    print()
    print(json.dumps({k: v for k, v in res.items() if k not in ("nouvelles", "scores_modifies")}, ensure_ascii=False, indent=1))
    if a.sortie:
        Path(a.sortie).write_bytes(construire_pack(r["departements"] or [p["departement"] for p in packs_publies()]))
        print(f"Pack écrit : {a.sortie}")


if __name__ == "__main__":
    _cli()
