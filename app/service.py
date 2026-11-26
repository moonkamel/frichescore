"""Orchestration : chargement des périmètres, calculs, portfolio, exports."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date

from . import db
from .demo.generate import generate
from .ingest.mapping import row_to_record
from .reports import pdf
from .scoring import config as C
from .scoring.analytics import heatmap, zan_clock
from .scoring.compare import compare, extension_site
from .scoring.engine import (best_usage, check_evaluable, evaluate_site, evaluate_usage, global_score,
                             normalize_weights, recommendations)
from .scoring.indicators import CATALOG, coerce
from .scoring.record import Provenance, Record
from .scoring.stress import PRESETS, base_ranking, monte_carlo, run_scenario

STAGES = {"reperee": "Repérée", "pre_etudiee": "Pré-étudiée", "en_etude": "En étude", "abandonnee": "Abandonnée"}


def load_demo(seed: int = 42):
    db.reset_all()
    communes, friches = generate(seed)
    db.bulk_load(communes, friches, "demo")


def ensure_seed():
    db.init()
    if db.is_empty():
        load_demo()


def _w(weights, d5):
    return normalize_weights(weights, d5)


# --------------------------------------------------------------- scores
def _compact_usage(ur: dict) -> dict:
    return {
        "dims": ur["dims"], "conf": ur["conf"], "blockers": ur["blockers"],
        "weak": {k: v[:2] for k, v in ur["weak"].items()},
        "contribs": {k: v[:4] for k, v in ur["contribs"].items()},
        "cout": {"bas": ur["cost"]["total_bas"], "haut": ur["cost"]["total_haut"], "m2_bas": ur["cost"]["eur_m2_bas"],
                 "m2_haut": ur["cost"]["eur_m2_haut"], "risque": ur["cost"]["risque"]},
        "delai": ur["friction"]["delai_total_mois"],
        "bilan": [ur["market"]["bilan_net_bas"], ur["market"]["bilan_net_haut"]],
        "elig": [e["dispositif"] for e in ur["impact"]["eligibilites"]],
    }


def scores(perimeter: dict) -> dict:
    sites = db.load_sites(perimeter)
    port = db.portfolio()
    out = []
    for sid, rec in sites.items():
        meta = {"id": sid, "nom": rec.get("nom"), "commune": rec.get("commune"), "code_insee": rec.get("code_insee"),
                "departement": rec.get("departement"), "epci": rec.get("epci"), "lat": rec.get("lat"), "lon": rec.get("lon"),
                "surface_m2": rec.get("surface_m2"), "type_friche": rec.get("type_friche"),
                "statut": rec.get("statut_cartofriches"), "origine": rec.get("_origine"),
                "portfolio": port.get(sid, {}).get("stage")}
        ev = evaluate_site(rec)
        if not ev["evaluable"]:
            out.append({**meta, "evaluable": False, "manquants": ev["manquants"]})
            continue
        out.append({**meta, "evaluable": True, "usages": {u: _compact_usage(ur) for u, ur in ev["usages"].items()}})
    return {"sites": out, "n": len(out)}


def site_detail(sid: str, weights: dict | None, d5: bool, usage: str | None = None,
                overrides: dict | None = None) -> dict:
    sites = db.load_sites({"type": "ids", "values": [sid]})
    if sid not in sites:
        raise KeyError(sid)
    rec = sites[sid]
    if overrides:
        ov = Record()
        for k, v in overrides.items():
            ov.set(k, coerce(k, v) if isinstance(v, str) else v,
                   Provenance("Hypothèse de contre-expertise (non enregistrée)", date.today().isoformat(), "moyen"))
        rec = rec.merged_with(ov)
    w = _w(weights, d5)
    base = {"values": rec.values, "prov": {k: p.to_dict() for k, p in rec.prov.items()},
            "fiche_vie": pdf.fiche_vie(rec.values, {k: p.to_dict() for k, p in rec.prov.items()}),
            "field": db.list_field(sid), "history": db.history(sid), "portfolio": db.portfolio().get(sid)}
    ev = evaluate_site(rec)
    if not ev["evaluable"]:
        return {**base, "evaluable": False, "manquants": ev["manquants"],
                "recommandations": recommendations(rec)}
    globals_ = {u: global_score(ur, w, d5) for u, ur in ev["usages"].items()}
    if usage is None or usage not in ev["usages"]:
        usage, _ = best_usage(ev, w, d5)
    ur = ev["usages"][usage]
    ranked = {sid: {"usage": usage, "g": globals_[usage], "ur": ur}}
    stages = {sid: (base["portfolio"] or {}).get("stage")}
    z = zan_clock(ranked, {sid: rec}, stages)["correspondances"]
    return {**base, "evaluable": True, "usage": usage, "usages": ev["usages"], "globals": globals_,
            "recommandations": recommendations(rec, ur), "zan": z, "weights": w}


# ---------------------------------------------------------- contre-analyse
def stress(perimeter, usage, weights, d5, hyp, top_n):
    sites = db.load_sites(perimeter)
    return run_scenario(sites, usage, _w(weights, d5), d5, hyp, top_n)


def mc(perimeter, usage, weights, d5, hyp, top_n, runs):
    sites = db.load_sites(perimeter)
    return monte_carlo(sites, usage, _w(weights, d5), d5, hyp, top_n, runs)


# ------------------------------------------------------------- jumeau
def compare_sites(a: dict, b: dict, weights, d5):
    def resolve(x):
        if x.get("extension"):
            p = x["extension"]
            com = db.get_commune(p.get("code_insee")) if p.get("code_insee") else None
            r = extension_site(p, com)
            if com is not None:
                r.values["commune"] = com.get("commune")
            return r
        s = db.load_sites({"type": "ids", "values": [x["id"]]})
        if x["id"] not in s:
            raise KeyError(x["id"])
        r = s[x["id"]]
        miss = check_evaluable(r)
        if miss:
            raise ValueError(f"{r.get('nom')} n'est pas évaluable : " + "; ".join(miss))
        return r
    ra, rb = resolve(a), resolve(b)
    return compare((ra, a["usage"]), (rb, b["usage"]), _w(weights, d5), d5)


# ------------------------------------------------------ vues agrégées
def heat(perimeter, usage, weights, d5, level):
    sites = db.load_sites(perimeter)
    ranked = base_ranking(sites, usage, _w(weights, d5), d5)
    return heatmap(ranked, sites, level)


def zan(perimeter, usage, weights, d5):
    sites = db.load_sites(perimeter)
    ranked = base_ranking(sites, usage, _w(weights, d5), d5)
    stages = {k: v["stage"] for k, v in db.portfolio().items()}
    ranked = {k: v for k, v in ranked.items() if stages.get(k) != "abandonnee"}
    return zan_clock(ranked, sites, stages)


# ------------------------------------------------------------ portfolio
def _snapshot(sid: str, motif: str, weights=None, d5=True):
    """Historique calculé avec une pondération FIXE (profil Équilibré) pour que
    l'évolution du score ne reflète que les changements de données."""
    weights, d5 = C.PROFILES["equilibre"]["weights"], True
    port = db.portfolio().get(sid) or {}
    sites_all = db.load_sites({"type": "ids", "values": [sid]})
    if sid not in sites_all:
        return
    rec = sites_all[sid]
    if check_evaluable(rec):
        return
    dep = rec.get("departement")
    peers = db.load_sites({"type": "departement", "values": [dep]})
    w = _w(weights, d5)
    usage = port.get("usage")
    if not usage:
        usage, _ = best_usage(evaluate_site(rec), w, d5)
    ranked = base_ranking(peers, usage, w, d5)
    order = sorted(ranked, key=lambda k: -ranked[k]["g"]["global"])
    if sid not in ranked:
        return
    db.add_history(sid, usage, ranked[sid]["g"]["global"], order.index(sid) + 1, len(order), ranked[sid]["ur"]["dims"], motif)


def portfolio_view(weights, d5):
    port = db.portfolio()
    if not port:
        return {"items": [], "stages": STAGES}
    sites = db.load_sites({"type": "ids", "values": list(port)})
    w = _w(weights, d5)
    items = []
    for sid, p in port.items():
        rec = sites.get(sid)
        if rec is None:
            continue
        ev = evaluate_site(rec)
        item = {"id": sid, "nom": rec.get("nom"), "commune": rec.get("commune"), "stage": p["stage"], "notes": p.get("notes"),
                "updated_at": p["updated_at"], "history": db.history(sid), "n_terrain": len(db.list_field(sid))}
        if ev["evaluable"]:
            u = p.get("usage") or best_usage(ev, w, d5)[0]
            g = global_score(ev["usages"][u], w, d5)
            item.update({"usage": u, "global": g["global"], "confiance": g["confiance"], "drivers": g["drivers"],
                         "dims": ev["usages"][u]["dims"]})
        else:
            item.update({"usage": p.get("usage"), "global": None, "manquants": ev["manquants"]})
        items.append(item)
    return {"items": items, "stages": STAGES}


def portfolio_set(sid, stage, usage, notes, weights, d5):
    if stage not in STAGES:
        raise ValueError("étape inconnue")
    prev = db.portfolio().get(sid)
    db.set_portfolio(sid, stage, usage, notes)
    if prev is None:
        _snapshot(sid, "Ajout au portfolio", weights, d5)
    elif prev["stage"] != stage:
        _snapshot(sid, f"Étape : {STAGES.get(prev['stage'], prev['stage'])} → {STAGES[stage]}", weights, d5)


def field_add(sid, key, value, source, date_, confiance, auteur, weights, d5):
    if key not in CATALOG:
        raise ValueError(f"indicateur inconnu : {key}")
    v = coerce(key, value)
    if v is None:
        raise ValueError("valeur invalide")
    allowed = CATALOG[key][4]
    if allowed and v not in allowed and not isinstance(v, list):
        raise ValueError(f"valeur attendue parmi : {', '.join(allowed)}")
    db.add_field(sid, key, v, source or "Donnée terrain", date_ or date.today().isoformat(), confiance or "eleve", auteur)
    if db.portfolio().get(sid) is None:
        db.set_portfolio(sid, "reperee", None, None)
    _snapshot(sid, f"Donnée terrain : {CATALOG[key][0]} = {v}", weights, d5)


def field_delete(sid, row_id, weights, d5):
    db.delete_field(sid, row_id)
    _snapshot(sid, "Suppression d'une donnée terrain", weights, d5)


# --------------------------------------------------------------- exports
def _ranking(perimeter, usage, weights, d5):
    sites = db.load_sites(perimeter)
    w = _w(weights, d5)
    ranked = base_ranking(sites, usage, w, d5)
    order = sorted(ranked, key=lambda k: -ranked[k]["g"]["global"])
    return sites, ranked, order, w


def export_csv(perimeter, usage, weights, d5) -> str:
    sites, ranked, order, w = _ranking(perimeter, usage, weights, d5)
    buf = io.StringIO()
    wr = csv.writer(buf, delimiter=";")
    wr.writerow(["rang", "id", "nom", "commune", "code_insee", "epci", "usage", "friche_score", "score_brut", "penalite_confiance",
                 "confiance", "potentiel_d1", "cout_d2", "friction_d3", "attractivite_d4", "impact_d5", "cout_bas_eur",
                 "cout_haut_eur", "delai_mois", "bilan_net_bas_eur", "bilan_net_haut_eur", "redhibitoire", "facteur_1",
                 "facteur_2", "facteur_3", "origine_donnees"])
    for i, sid in enumerate(order, 1):
        b = ranked[sid]
        ur, g, rec = b["ur"], b["g"], sites[sid]
        dr = [f"{d['label']} ({d['effet']:+.1f})" for d in g["drivers"]] + ["", "", ""]
        wr.writerow([i, sid, rec.get("nom"), rec.get("commune"), rec.get("code_insee"), rec.get("epci"), b["usage"], g["global"],
                     g["brut"], g["penalite"], g["confiance"], *[ur["dims"][k] for k in ("d1", "d2", "d3", "d4", "d5")],
                     ur["cost"]["total_bas"], ur["cost"]["total_haut"], ur["friction"]["delai_total_mois"],
                     ur["market"]["bilan_net_bas"], ur["market"]["bilan_net_haut"], "; ".join(ur["blockers"]), *dr[:3],
                     rec.get("_origine")])
    return buf.getvalue()


def site_pdf(sid, usage, weights, d5, profile):
    d = site_detail(sid, weights, d5, usage)
    if not d["evaluable"]:
        raise ValueError("Site non évaluable : " + "; ".join(d["manquants"]))
    return pdf.site_pdf(d, d["usage"], d["globals"][d["usage"]], d["weights"], C.PROFILES.get(profile, {}).get("label", "personnalisé"))


def report_pdf(perimeter, perimeter_label, usage, weights, d5, top_n, profile):
    sites, ranked, order, w = _ranking(perimeter, usage, weights, d5)
    stress_res = run_scenario(sites, usage, w, d5, PRESETS["defavorable"]["hyp"], top_n)
    mc_res = monte_carlo(sites, usage, w, d5, {}, top_n, runs=60)
    rob = {r["id"]: f"{r['robustesse']} ({r['p_top']:.0%})" for r in mc_res["lignes"]}
    top = []
    for i, sid in enumerate(order[:top_n], 1):
        b = ranked[sid]
        ur = b["ur"]
        top.append({"rang": i, "id": sid, "nom": sites[sid].get("nom"), "commune": sites[sid].get("commune"),
                    "usage": b["usage"], "global": b["g"]["global"], "dims": ur["dims"], "confiance": b["g"]["confiance"],
                    "cout_bas": ur["cost"]["eur_m2_bas"], "cout_haut": ur["cost"]["eur_m2_haut"],
                    "delai": ur["friction"]["delai_total_mois"], "drivers": b["g"]["drivers"], "phrases": ur["phrases"],
                    "recos": recommendations(sites[sid], ur), "blockers": ur["blockers"], "robustesse": rob.get(sid, "-")})
    non_eval = []
    for sid, rec in sites.items():
        m = check_evaluable(rec)
        if m:
            non_eval.append({"id": sid, "nom": rec.get("nom"), "commune": rec.get("commune"), "manquants": m})
    src = defaultdict(lambda: {"n": 0, "dates": set(), "conf": set()})
    for sid in order[:top_n]:
        for k, p in sites[sid].prov.items():
            s = src[p.source]
            s["n"] += 1
            if p.date:
                s["dates"].add(str(p.date)[:4])
            s["conf"].add(p.confiance)
    sources = [{"source": k, "n": v["n"], "dates": ", ".join(sorted(v["dates"])) or "n.r.",
                "confiance": "/".join(sorted(v["conf"]))} for k, v in sorted(src.items(), key=lambda x: -x[1]["n"])]
    ctx = {"perimetre_label": perimeter_label, "usage_label": C.USAGES.get(usage, "meilleur usage par site") if usage else "meilleur usage par site",
           "profil": C.PROFILES.get(profile, {}).get("label", "personnalisé"), "weights": w, "top": top,
           "stats": {"n": len(sites), "evaluables": len(ranked), "non_evaluables": len(non_eval)},
           "stress": stress_res, "mc": mc_res, "non_evaluables": non_eval, "sources": sources,
           "demo": any(str(r.get("_origine", "")).startswith("demo") for r in sites.values())}
    return pdf.report_pdf(ctx)


# ---------------------------------------------------------------- import
def import_csv(text: str, origine_label: str, default_date: str | None) -> dict:
    sample = text[:4096]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    n, ignored, errors = 0, set(), []
    slug = "import:" + (origine_label or "csv")
    for i, row in enumerate(reader, 1):
        rec, ign = row_to_record(row, origine_label or "import CSV", default_date)
        ignored.update(ign)
        if not rec.has("id"):
            rec.values["id"] = f"IMP-{rec.get('code_insee', 'X')}-{i:05d}"
        if not rec.has("code_insee"):
            errors.append(f"ligne {i} : code INSEE manquant")
        db.upsert_friche(rec, slug)
        n += 1
    return {"importees": n, "colonnes_ignorees": sorted(ignored), "avertissements": errors[:50]}


def csv_template() -> str:
    cols = ["id"] + [k for k in CATALOG]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cols)
    w.writerow(["EX-001", "Ancienne usine exemple", "Exempleville", "59999", "59", "CA Exemple", "50.5", "3.1", "25000",
                "industrielle", "verifiee", "métallurgie"] + [""] * (len(cols) - 12))
    return buf.getvalue()


def catalog():
    return {k: {"label": v[0], "type": v[1], "source": v[2], "confiance": v[3], "valeurs": v[4]} for k, v in CATALOG.items()}
