"""Tâches de fond (imports, mises à jour) : exécutées dans des fils, suivies par l'interface."""
from __future__ import annotations

import threading
import time
import uuid

JOBS: dict[str, dict] = {}


def start_job(fn, libelle: str = "Tâche") -> dict:
    """Exécute `fn(progress)` en tâche de fond ; suivi via GET /api/ingest/jobs/{id}."""
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "libelle": libelle, "etat": "en_cours", "etape": "Démarrage", "fait": 0, "total": 0, "rapport": None,
           "erreur": None, "debut": time.time(), "duree_s": None}
    JOBS[jid] = job
    for old in sorted(JOBS, key=lambda k: JOBS[k]["debut"])[:-50]:  # on garde les 50 dernières tâches
        if JOBS[old]["etat"] != "en_cours":
            JOBS.pop(old, None)

    def prog(etape, fait, total):
        job.update(etape=etape, fait=fait, total=total)

    def run():
        try:
            job["rapport"] = fn(prog)
            job["etat"] = "termine"
        except Exception as e:  # noqa: BLE001
            job.update(etat="erreur", erreur=f"{type(e).__name__} : {e}")
        job["duree_s"] = round(time.time() - job["debut"])

    threading.Thread(target=run, daemon=True).start()
    return job


def running(libelle_prefix: str) -> dict | None:
    return next((j for j in JOBS.values() if j["etat"] == "en_cours" and j["libelle"].startswith(libelle_prefix)), None)
