"""API FricheScore (FastAPI). Sert aussi l'interface web statique."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, service
from .ingest import connectors
from .scoring import config as C
from .scoring.analytics import load_windows
from .scoring.stress import PRESETS

FRONT = Path(__file__).resolve().parent.parent / "frontend"

@asynccontextmanager
async def lifespan(_app):
    service.ensure_seed()
    yield


app = FastAPI(title="FricheScore", version="0.1.0", lifespan=lifespan,
              description="Moteur de priorisation des friches : score à 5 dimensions, explicable et tracé.")
app.add_middleware(GZipMiddleware, minimum_size=1000)


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


@app.post("/api/ingest/cartofriches")
def ingest_cartofriches(r: CartoReq):
    """Interroge Cartofriches, puis Géorisques et DVF. Nécessite un accès réseau ;
    les échecs sont rapportés sans interrompre l'import."""
    return service.ingest_cartofriches([c.strip() for c in r.codes_insee if c.strip()], r.georisques, r.dvf)


class ConnTestReq(BaseModel):
    source: str
    code_insee: str = "59350"
    lat: float = 50.6329
    lon: float = 3.0586
    base_url: str | None = None


@app.post("/api/connecteurs/test")
def connector_test(r: ConnTestReq):
    if r.source not in ("cartofriches", "georisques", "dvf"):
        raise HTTPException(400, "source inconnue (cartofriches, georisques, dvf)")
    if r.base_url and not r.base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "l'adresse doit commencer par http:// ou https://")
    return connectors.test_connection(r.source, r.code_insee.strip(), r.lat, r.lon, r.base_url or None)


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


@app.exception_handler(Exception)
async def _err(request, exc):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# ------------------------------------------------------------------- front
@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


app.mount("/static", StaticFiles(directory=FRONT), name="static")
