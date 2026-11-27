"""API FricheScore (FastAPI). Sert aussi l'interface web statique."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.routing import APIRoute
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, service, tenant
from .ingest import connectors
from .scoring import config as C
from .scoring.analytics import load_windows
from .scoring.stress import PRESETS

FRONT = Path(__file__).resolve().parent.parent / "frontend"

@asynccontextmanager
async def lifespan(_app):
    service.ensure_seed()
    from . import maj
    maj.demarrer_planificateur()
    yield


app = FastAPI(title="FricheScore", version="0.1.0", lifespan=lifespan,
              description="Moteur de priorisation des friches : score à 5 dimensions, explicable et tracé.")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(auth.router)


@app.middleware("http")
async def _contexte(request: Request, call_next):
    """Organisme et utilisateur de la requête ; contrôle des rôles en mode en ligne."""
    path, method = request.url.path, request.method
    if not tenant.auth_enabled():
        t = tenant.set_context(tenant.LOCAL, None)
        try:
            return await call_next(request)
        finally:
            tenant.reset_context(t)
    if not path.startswith("/api/") or (method, path) in auth.OUVERT:
        return await call_next(request)
    u = auth.user_from_request(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "connexion requise"})
    if not auth.allowed(u["role"], method, path):
        return JSONResponse(status_code=403, content={"detail": f"action réservée au rôle « {tenant.ROLE_LBL[auth.required_role(method, path)]} »"})
    org = tenant.PLATEFORME if u["role"] == "admin_plateforme" else u["organisme_id"]
    t = tenant.set_context(org, u)
    try:
        return await call_next(request)
    finally:
        tenant.reset_context(t)


class Perimeter(BaseModel):
    type: str = "tout"  # tout | departement | epci | communes | ids
    values: list[str] = Field(default_factory=list)


class Ctx(BaseModel):
    perimeter: Perimeter = Field(default_factory=Perimeter)
    usage: str | None = None  # None = meilleur usage par site
    weights: dict[str, float] | None = None
    d5: bool = True
    profile: str | None = None


class StressReq(Ctx):
    hyp: dict[str, Any] = Field(default_factory=dict)
    preset: str | None = None
    top_n: int = 10
    runs: int = 100


class SiteReq(BaseModel):
    usage: str | None = None
    weights: dict[str, float] | None = None
    d5: bool = True
    overrides: dict[str, Any] | None = None
    profile: str | None = None


class CompareSide(BaseModel):
    id: str | None = None
    usage: str
    extension: dict[str, Any] | None = None


class CompareReq(BaseModel):
    a: CompareSide
    b: CompareSide
    weights: dict[str, float] | None = None
    d5: bool = True


class PortfolioReq(BaseModel):
    stage: str
    usage: str | None = None
    notes: str | None = None
    weights: dict[str, float] | None = None
    d5: bool = True


class FieldReq(BaseModel):
    key: str
    value: Any
    source: str | None = None
    date: str | None = None
    confiance: str | None = "eleve"
    auteur: str | None = None
    weights: dict[str, float] | None = None
    d5: bool = True


class ReportReq(Ctx):
    perimeter_label: str = "Tout le référentiel"
    top_n: int = 10


def _usage(u):
    if u and u not in C.USAGES:
        raise HTTPException(400, f"usage inconnu : {u}")
    return u or None


# ------------------------------------------------------------------- méta
@app.get("/api/meta")
def meta():
    return {"usages": C.USAGES, "dimensions": C.DIMENSIONS, "profiles": C.PROFILES,
            "presets": {k: v["label"] for k, v in PRESETS.items()}, "stages": service.STAGES,
            "catalog": service.catalog(), "perimeters": db.perimeters(),
            "constants": {"confidence_max_penalty": C.CONFIDENCE_MAX_PENALTY, "blocker_cap": C.BLOCKER_CAP,
                          "piegeux_d2_max": C.PIEGEUX_D2_MAX}}


@app.post("/api/scores")
def scores(p: Perimeter):
    return service.scores(p.model_dump())


@app.post("/api/sites/{sid}")
def site(sid: str, r: SiteReq):
    try:
        return service.site_detail(sid, r.weights, r.d5, _usage(r.usage), r.overrides)
    except KeyError:
        raise HTTPException(404, "site introuvable")


@app.post("/api/sites/{sid}/pdf")
def site_pdf(sid: str, r: SiteReq):
    try:
        data = service.site_pdf(sid, _usage(r.usage), r.weights, r.d5, r.profile)
    except KeyError:
        raise HTTPException(404, "site introuvable")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="frichescore_{sid}.pdf"'})


@app.post("/api/stress")
def stress(r: StressReq):
    hyp = dict(PRESETS[r.preset]["hyp"]) if r.preset in PRESETS else {}
    hyp.update(r.hyp or {})
    if hyp.get("usage_force") and hyp["usage_force"] not in C.USAGES:
        hyp.pop("usage_force")
    return service.stress(r.perimeter.model_dump(), _usage(r.usage), r.weights, r.d5, hyp, r.top_n)


@app.post("/api/montecarlo")
def montecarlo(r: StressReq):
    hyp = dict(PRESETS[r.preset]["hyp"]) if r.preset in PRESETS else {}
    hyp.update(r.hyp or {})
    return service.mc(r.perimeter.model_dump(), _usage(r.usage), r.weights, r.d5, hyp, r.top_n, max(20, min(400, r.runs)))


@app.post("/api/compare")
def compare(r: CompareReq):
    for s in (r.a, r.b):
        _usage(s.usage)
        if not s.id and not s.extension:
            raise HTTPException(400, "chaque côté doit désigner une friche ou un site en extension")
    try:
        return service.compare_sites(r.a.model_dump(), r.b.model_dump(), r.weights, r.d5)
    except KeyError:
        raise HTTPException(404, "site introuvable")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/heatmap")
def heatmap(r: Ctx, level: str = "epci"):
    if level not in ("epci", "departement", "commune"):
        raise HTTPException(400, "niveau inconnu")
    return service.heat(r.perimeter.model_dump(), _usage(r.usage), r.weights, r.d5, level)


class EtudesReq(Ctx):
    top_n: int = 10
    budget_keur: float | None = None


@app.post("/api/etudes")
def etudes(r: EtudesReq):
    """Quelle étude lancer en premier (valeur de l'information pour la décision)."""
    return service.etudes(r.perimeter.model_dump(), _usage(r.usage), r.weights, r.d5, max(1, min(100, r.top_n)),
                          r.budget_keur if r.budget_keur and r.budget_keur > 0 else None)


@app.post("/api/zan")
def zan(r: Ctx):
    return service.zan(r.perimeter.model_dump(), _usage(r.usage), r.weights, r.d5)


@app.get("/api/zan/calendrier")
def zan_calendar():
    return load_windows()


# -------------------------------------------------------------- portfolio
@app.post("/api/portfolio/list")
def portfolio_list(r: SiteReq):
    return service.portfolio_view(r.weights, r.d5)


@app.put("/api/portfolio/{sid}")
def portfolio_put(sid: str, r: PortfolioReq):
    try:
        service.portfolio_set(sid, r.stage, _usage(r.usage), r.notes, r.weights, r.d5)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.delete("/api/portfolio/{sid}")
def portfolio_del(sid: str):
    db.remove_portfolio(sid)
    return {"ok": True}


@app.post("/api/sites/{sid}/terrain")
def field_add(sid: str, r: FieldReq):
    try:
        service.field_add(sid, r.key, r.value, r.source, r.date, r.confiance, r.auteur, r.weights, r.d5)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.delete("/api/sites/{sid}/terrain/{row_id}")
def field_del(sid: str, row_id: int):
    service.field_delete(sid, row_id, None, True)
    return {"ok": True}


# ---------------------------------------------------------------- exports
@app.post("/api/export/csv")
def export_csv(r: Ctx):
    data = service.export_csv(r.perimeter.model_dump(), _usage(r.usage), r.weights, r.d5)
    return Response("﻿" + data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="frichescore_classement.csv"'})


@app.post("/api/export/rapport")
def export_report(r: ReportReq):
    data = service.report_pdf(r.perimeter.model_dump(), r.perimeter_label, _usage(r.usage), r.weights, r.d5,
                              max(3, min(30, r.top_n)), r.profile)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="frichescore_rapport_precadrage.pdf"'})


# ----------------------------------------------------------------- données
@app.get("/api/import/modele.csv")
def import_template():
    return Response("﻿" + service.csv_template(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="frichescore_modele_import.csv"'})


@app.get("/api/import/dictionnaire.csv")
def import_dictionary():
    return Response("\ufeff" + service.csv_dictionary(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="frichescore_dictionnaire_colonnes.csv"'})


@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...), source: str = Form("import CSV"), date: str | None = Form(None),
                     dry_run: bool = Form(False)):
    raw = await file.read()
    if len(raw) > 30 * 1024 * 1024:
        raise HTTPException(413, "fichier trop volumineux (30 Mo maximum)")
    text, enc = service.decode_bytes(raw)
    return service.import_csv(text, source or file.filename, date or None, dry_run, enc)


class CartoReq(BaseModel):
    codes_insee: list[str]
    georisques: bool = True
    dvf: bool = True
    communes: bool = True
    sites: bool = True


from .jobs import JOBS as _JOBS  # noqa: E402
from .jobs import start_job as _start_job  # noqa: E402


@app.post("/api/ingest/cartofriches")
def ingest_cartofriches(r: CartoReq):
    """Lance l'import en tâche de fond (Cartofriches, Géorisques, DVF, indicateurs communaux)."""
    codes = [c.strip() for c in r.codes_insee if c.strip()]
    if not codes:
        raise HTTPException(400, "aucun code INSEE")
    return _start_job(lambda prog: service.ingest_cartofriches(codes, r.georisques, r.dvf, prog, r.communes, r.sites),
                      f"Import de périmètre ({', '.join(codes[:6])}{'…' if len(codes) > 6 else ''})")


class SitesReq(BaseModel):
    ids: list[str] | None = None
    adresse: bool = True


@app.post("/api/enrichir/sites")
def sites_enrich(r: SitesReq):
    """Indicateurs de site (gare, transports, ensoleillement, risques) pour les friches réelles."""
    return _start_job(lambda prog: service.enrich_sites(r.ids or None, r.adresse, prog), "Indicateurs de site")


# ---------------------------------------------------------- extraction IA
@app.get("/api/ia/statut")
def ia_status():
    return service.ia_status()


@app.post("/api/sites/{sid}/documents")
async def ia_upload(sid: str, file: UploadFile = File(...), consentement: bool = Form(False)):
    """Analyse d'un document PDF par l'IA (tâche de fond). Le consentement est obligatoire :
    le document est transmis à l'API Anthropic."""
    from .ia.extraction import IAError
    if not consentement:
        raise HTTPException(400, "consentement requis : le document est transmis à l'API Anthropic")
    data = await file.read()
    try:
        eid, run = service.ia_start(sid, file.filename or "document.pdf", data)
    except KeyError:
        raise HTTPException(404, "site introuvable")
    except IAError as e:
        raise HTTPException(422, str(e))
    job = _start_job(run, f"Analyse IA : {file.filename or 'document'}")
    return {**job, "extraction": eid}


@app.get("/api/sites/{sid}/documents")
def ia_list(sid: str):
    return {"extractions": db.ia_list(sid), "statut": service.ia_status()}


class IAValidReq(BaseModel):
    items: list[dict[str, Any]]
    auteur: str | None = None
    weights: dict[str, float] | None = None
    d5: bool = True


@app.post("/api/sites/{sid}/documents/{eid}/valider")
def ia_validate(sid: str, eid: str, r: IAValidReq):
    try:
        return service.ia_validate(sid, eid, r.items, r.auteur, r.weights, r.d5)
    except KeyError:
        raise HTTPException(404, "extraction introuvable")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/documents/{eid}.pdf")
def ia_pdf(eid: str):
    ex = db.ia_get(eid)
    p = db.DB_PATH.parent / "documents" / f"{(ex or {}).get('sha256')}.pdf"
    if not ex or not p.exists():
        raise HTTPException(404, "document introuvable")
    return FileResponse(p, media_type="application/pdf", filename=ex["fichier"],
                        content_disposition_type="inline")


class QuestionReq(Ctx):
    question: str
    perimeter_label: str = "Tout le référentiel"


@app.post("/api/ia/question")
def ia_question(r: QuestionReq):
    """Contre-analyse en langage naturel : l'IA traduit la question en hypothèses, le moteur calcule."""
    from .ia.client import IAError
    try:
        return service.ia_question(r.question, r.perimeter.model_dump(), r.perimeter_label, _usage(r.usage), r.weights, r.d5)
    except IAError as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/sites/{sid}/note")
def ia_note(sid: str, r: SiteReq):
    """Note d'analyse rédigée par l'IA à partir des données du site (usage affiché)."""
    from .ia.client import IAError
    try:
        return service.ia_note(sid, _usage(r.usage), r.weights, r.d5)
    except KeyError:
        raise HTTPException(404, "site introuvable")
    except (IAError, ValueError) as e:
        raise HTTPException(422, str(e))


@app.post("/api/ia/jumeau")
def ia_jumeau(r: CompareReq):
    from .ia.client import IAError
    try:
        return service.ia_jumeau(r.a.model_dump(), r.b.model_dump(), r.weights, r.d5)
    except KeyError:
        raise HTTPException(404, "site introuvable")
    except (IAError, ValueError) as e:
        raise HTTPException(422, str(e))


class CommunesReq(BaseModel):
    codes_insee: list[str] | None = None
    rafraichir: bool = False


@app.post("/api/communes/enrichir")
def communes_enrich(r: CommunesReq):
    """Met à jour les indicateurs communaux réels (toutes les communes portant des friches réelles par défaut)."""
    codes = [c.strip() for c in (r.codes_insee or []) if c.strip()] or None
    return _start_job(lambda prog: service.enrich_communes(codes, prog, r.rafraichir), "Indicateurs communaux")


@app.get("/api/ingest/jobs")
def ingest_jobs():
    """Tâches de fond : en cours, puis terminées (les plus récentes d'abord)."""
    from .jobs import visible
    jobs = sorted(visible(), key=lambda j: (j["etat"] != "en_cours", -j["debut"]))
    return {"taches": [{k: v for k, v in j.items() if k != "rapport"} | {"a_un_rapport": j["rapport"] is not None} for j in jobs[:20]]}


@app.get("/api/ingest/jobs/{jid}")
def ingest_job(jid: str):
    from .jobs import visible
    j = next((x for x in visible() if x["id"] == jid), None)
    if j is None:
        raise HTTPException(404, "tâche inconnue (le serveur a peut-être redémarré)")
    return j


class ConnTestReq(BaseModel):
    source: str
    code_insee: str = "59350"
    lat: float = 50.6329
    lon: float = 3.0586
    base_url: str | None = None
    timeout: float | None = None
    siren: str | None = None


@app.post("/api/connecteurs/test")
def connector_test(r: ConnTestReq):
    if r.source not in ("cartofriches", "georisques", "dvf", "communes", "sites", "bodacc"):
        raise HTTPException(400, "source inconnue (cartofriches, georisques, dvf, communes, sites, bodacc)")
    if r.source == "bodacc":
        from . import signaux as SG
        return SG.test_bodacc(r.siren or "")
    if r.source == "sites":
        from .ingest import site as S_
        return S_.test_site(r.lat, r.lon, r.code_insee.strip())
    if r.source == "communes":
        from .ingest import territoire
        return territoire.test_communes(r.code_insee.strip())
    if r.base_url and not r.base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "l'adresse doit commencer par http:// ou https://")
    t = max(5.0, min(180.0, r.timeout)) if r.timeout else None
    return connectors.test_connection(r.source, r.code_insee.strip(), r.lat, r.lon, r.base_url or None, t)


@app.get("/api/connecteurs")
def connector_info():
    return {"cartofriches": connectors.CARTOFRICHES_URL, "georisques": connectors.GEORISQUES_URL,
            "dvf": connectors.GEODVF_URL, "timeout_s": connectors.TIMEOUT}


# ------------------------------------------------------------- paramètres
class ParamChange(BaseModel):
    path: str
    value: Any = None
    note: str | None = None


class ParamsReq(BaseModel):
    changes: list[ParamChange]


@app.get("/api/parametres")
def params_get():
    return service.params_view()


@app.put("/api/parametres")
def params_put(r: ParamsReq):
    return service.params_save([c.model_dump() for c in r.changes])


@app.post("/api/parametres/reset")
def params_reset(path: str | None = None):
    service.params_reset(path)
    return service.params_view()


@app.get("/api/parametres/export")
def params_export():
    import json as _json
    return Response(_json.dumps(service.params_export(), ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="frichescore_parametres.json"'})


@app.post("/api/parametres/import")
def params_import(data: dict):
    try:
        return service.params_import(data)
    except ValueError as e:
        raise HTTPException(422, str(e))


# -------------------------------------------------------------- calendrier
@app.get("/api/calendrier")
def calendar_get():
    return service.calendar_view()


@app.put("/api/calendrier")
def calendar_put(data: dict):
    errs = service.calendar_save(data)
    if errs:
        raise HTTPException(422, {"erreurs": errs})
    return service.calendar_view()


@app.post("/api/calendrier/reset")
def calendar_reset():
    from .scoring import calendar as cal
    cal.reset()
    return service.calendar_view()


@app.post("/api/demo/reset")
def demo_reset():
    service.load_demo()
    return {"ok": True}


@app.delete("/api/donnees/{origine}")
def delete_origin(origine: str):
    db.delete_origin(origine)
    return {"ok": True}


# ------------------------------------------------------- signaux d'opportunité
@app.get("/api/signaux")
def signaux_list(non_vus: bool = False):
    return service.signaux_view(non_vus)


class SignauxVuReq(BaseModel):
    ids: list[int] | None = None  # None = tous
    vu: bool = True


@app.post("/api/signaux/vu")
def signaux_vu(r: SignauxVuReq):
    db.signals_mark(r.ids, r.vu)
    return service.signaux_view()


@app.post("/api/signaux/rechercher")
def signaux_search():
    """Recherche BODACC immédiate pour toutes les friches réelles ayant un SIREN connu (tâche de fond)."""
    return _start_job(service.signaux_rechercher, "Signaux : annonces BODACC")


# ------------------------------------------------------- mise à jour automatique et packs
@app.get("/api/maj")
def maj_get():
    from . import maj
    return maj.etat()


@app.put("/api/maj")
def maj_put(data: dict):
    from . import maj
    try:
        maj.enregistrer(data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return maj.etat()


@app.post("/api/maj/lancer")
def maj_run():
    from . import maj
    return maj.lancer("manuel")


@app.get("/api/packs/export")
def pack_export(departements: str):
    from . import maj
    deps = [d for d in departements.replace(";", ",").split(",") if d.strip()]
    try:
        raw = maj.construire_pack(deps)
    except ValueError as e:
        raise HTTPException(400, str(e))
    name = f"frichescore_pack_{'-'.join(sorted(maj.norm_dep(d) for d in deps))}_{db.now()[:10]}.json.gz"
    return Response(raw, media_type="application/gzip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/packs/import")
async def pack_import(file: UploadFile = File(...), dry_run: bool = Form(False)):
    from . import maj
    raw = await file.read()
    if len(raw) > maj.MAX_PACK_BYTES:
        raise HTTPException(413, "pack trop volumineux")
    try:
        return maj.importer_pack(raw, dry_run)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/packs")
def packs_list():
    from . import maj
    return {"packs": maj.packs_publies()}


@app.get("/api/packs/{nom}")
def pack_get(nom: str):
    """Packs publiés par ce serveur (mode « serveur central ») : /api/packs/59.json.gz"""
    from . import maj
    dep = nom.removesuffix(".json.gz").upper()
    f = maj.packs_dir() / f"{dep}.json.gz"
    if not maj.DEP_RE.match(dep) or not f.exists():
        raise HTTPException(404, "aucun pack publié pour ce département")
    return FileResponse(f, media_type="application/gzip", filename=f"{dep}.json.gz")


@app.exception_handler(KeyError)
async def _introuvable(request, exc):
    return JSONResponse(status_code=404, content={"detail": f"introuvable : {exc}"})


@app.exception_handler(PermissionError)
async def _interdit(request, exc):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def _err(request, exc):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# ------------------------------------------------------------------- paramètres par organisme
def _with_org_params(fn):
    """Chaque appel calcule avec les paramètres de son organisme (voir tenant.PARAMS)."""
    import functools

    @functools.wraps(fn)
    def run(*a, **kw):
        with tenant.PARAMS.use(tenant.org()):
            return fn(*a, **kw)
    return run


for _r in app.routes:
    if isinstance(_r, APIRoute) and _r.path.startswith("/api/") and not _r.path.startswith("/api/auth") \
            and not __import__("asyncio").iscoroutinefunction(_r.dependant.call):
        _r.dependant.call = _with_org_params(_r.dependant.call)


# ------------------------------------------------------------------- front
@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


app.mount("/static", StaticFiles(directory=FRONT), name="static")
