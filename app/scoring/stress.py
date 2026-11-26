"""Mode « contre-analyse » : stress-test du classement.

Deux mécanismes complémentaires :
  1. Scénario déterministe : l'utilisateur durcit une ou plusieurs hypothèses
     (dépollution ×2, délais +12 mois, prix -10 %, pas de subvention, lecture
     pessimiste des données incertaines, changement d'usage) et voit quels sites
     restent en tête.
  2. Simulation de Monte-Carlo : chaque site est rejoué N fois en tirant son
     coût dans sa fourchette, un choc de marché et des délais aléatoires
     proportionnels à l'INCERTITUDE de ses propres données. On obtient une
     probabilité de rester dans le top N : un site bien documenté est plus
     robuste qu'un site au score équivalent mais construit sur des données fragiles.

L'usage retenu en situation de base est conservé dans les scénarios (on teste le
même projet), sauf si le scénario impose un usage.
"""
from __future__ import annotations

import random
from datetime import date

from .config import DIMENSIONS, USAGES
from .engine import best_usage, evaluate_site, evaluate_usage, global_score
from .record import Record

PRESETS = {
    "defavorable": {"label": "Défavorable combiné", "hyp": {"cout_depollution_mult": 2.0, "delai_supplementaire_mois": 12, "prix_marche_pct": -10}},
    "depollution_x2": {"label": "Dépollution ×2", "hyp": {"cout_depollution_mult": 2.0}},
    "delais_12": {"label": "Délais +12 mois", "hyp": {"delai_supplementaire_mois": 12}},
    "marche_baisse": {"label": "Marché -15 %", "hyp": {"prix_marche_pct": -15}},
    "sans_subventions": {"label": "Sans subventions", "hyp": {"sans_subventions": True}},
    "pessimiste": {"label": "Lecture pessimiste des données incertaines", "hyp": {"pessimiste": True, "cout_depollution_mult": 1.3}},
}


def _rank(scores: dict[str, float]) -> dict[str, int]:
    order = sorted(scores, key=lambda k: -scores[k])
    return {k: i + 1 for i, k in enumerate(order)}


def base_ranking(sites: dict[str, Record], usage: str | None, weights: dict, d5: bool,
                 today: date | None = None) -> dict[str, dict]:
    out = {}
    for sid, rec in sites.items():
        ev = evaluate_site(rec, usages=[usage] if usage else None, today=today)
        if not ev["evaluable"]:
            continue
        if usage:
            ur = ev["usages"][usage]
            g = global_score(ur, weights, d5)
            u = usage
        else:
            u, g = best_usage(ev, weights, d5)
            ur = ev["usages"][u]
        out[sid] = {"usage": u, "g": g, "ur": ur}
    return out


def _why(base_ur: dict, scn_ur: dict, weights: dict) -> str:
    deltas = []
    for d in ("d1", "d2", "d3", "d4", "d5"):
        b, s = base_ur["dims"][d], scn_ur["dims"][d]
        diff = (b - s) if d != "d3" else (s - b)  # friction : hausse = dégradation
        deltas.append((diff * float(weights.get(d, 0)), d, b, s))
    deltas.sort(reverse=True)
    w, d, b, s = deltas[0]
    if w <= 0.5:
        return "classement relatif : d'autres sites progressent"
    return f"{DIMENSIONS[d].lower()} passe de {b:.0f} à {s:.0f}"


def run_scenario(sites: dict[str, Record], usage: str | None, weights: dict, d5: bool,
                 hyp: dict, top_n: int = 10, today: date | None = None) -> dict:
    base = base_ranking(sites, usage, weights, d5, today)
    force = hyp.get("usage_force")
    scn = {}
    for sid, b in base.items():
        u = force or b["usage"]
        ur = evaluate_usage(sites[sid], u, hyp, today)
        scn[sid] = {"usage": u, "g": global_score(ur, weights, d5), "ur": ur}
    rb = _rank({k: v["g"]["global"] for k, v in base.items()})
    rs = _rank({k: v["g"]["global"] for k, v in scn.items()})
    top_base = [k for k, r in sorted(rb.items(), key=lambda x: x[1]) if r <= top_n]
    rows = []
    for sid in top_base:
        db = base[sid]["g"]["global"]
        ds = scn[sid]["g"]["global"]
        r0, r1 = rb[sid], rs[sid]
        if r1 > 2 * top_n or ds < db - 20:
            statut = "s'effondre"
        elif r1 > top_n:
            statut = "sort du top"
        elif abs(r1 - r0) > 2:
            statut = "fragile"
        else:
            statut = "stable"
        rows.append({"id": sid, "nom": sites[sid].get("nom"), "commune": sites[sid].get("commune"),
                     "usage_base": base[sid]["usage"], "usage_scenario": scn[sid]["usage"],
                     "rang_base": r0, "rang_scenario": r1, "score_base": db, "score_scenario": ds,
                     "delta": round(ds - db, 1), "statut": statut,
                     "pourquoi": _why(base[sid]["ur"], scn[sid]["ur"], weights) if statut != "stable" else ""})
    entrants = [{"id": k, "nom": sites[k].get("nom"), "commune": sites[k].get("commune"), "rang_base": rb[k],
                 "rang_scenario": r, "score_scenario": scn[k]["g"]["global"]}
                for k, r in sorted(rs.items(), key=lambda x: x[1]) if r <= top_n and rb[k] > top_n]
    n_stay = sum(1 for r in rows if r["rang_scenario"] <= top_n)

    def _names(items, fmt, k=5):
        txt = ", ".join(fmt(r) for r in items[:k])
        return txt + (f" et {len(items) - k} autre(s)" if len(items) > k else "")
    stables = [r for r in rows if r["statut"] == "stable"]
    out_ = [r for r in rows if r["statut"] in ("sort du top", "s'effondre")]
    synth = f"{n_stay} des {len(rows)} sites du top {top_n} y restent sous ce scénario."
    if stables:
        synth += " Sites robustes : " + _names(stables, lambda r: r["nom"]) + "."
    if out_:
        synth += " Sites qui décrochent : " + _names(out_, lambda r: f"{r['nom']} ({r['pourquoi']})") + "."
    return {"lignes": rows, "entrants": entrants, "synthese": synth, "hypotheses": hyp, "top_n": top_n}


def monte_carlo(sites: dict[str, Record], usage: str | None, weights: dict, d5: bool,
                hyp: dict | None = None, top_n: int = 10, runs: int = 100, seed: int = 7,
                today: date | None = None) -> dict:
    """Probabilité de rester dans le top N quand les incertitudes propres à
    chaque site sont tirées au hasard. Pour rester rapide, seuls les
    max(3N, 40) premiers sites du classement de base sont simulés : les
    suivants ont une probabilité négligeable d'entrer dans le top N."""
    hyp = hyp or {}
    rng = random.Random(seed)
    base = base_ranking(sites, usage, weights, d5, today)
    rb = _rank({k: v["g"]["global"] for k, v in base.items()})
    cands = [k for k, r in sorted(rb.items(), key=lambda x: x[1])][: max(3 * top_n, 40)]
    hits = {k: 0 for k in cands}
    scores = {k: [] for k in cands}
    for _ in range(runs):
        sc = {}
        for sid in cands:
            ur0 = base[sid]["ur"]
            c4, c3 = ur0["conf"]["d4"], ur0["conf"]["d3"]
            h = dict(hyp)
            h["cost_quantile"] = rng.random()
            h["market_noise"] = rng.gauss(0, max(0.03, (1 - c4) * 0.25))
            h["delai_supplementaire_mois"] = float(hyp.get("delai_supplementaire_mois", 0)) + rng.uniform(0, (1 - c3) * 18)
            ur = evaluate_usage(sites[sid], hyp.get("usage_force") or base[sid]["usage"], h, today)
            sc[sid] = global_score(ur, weights, d5)["global"]
        r = _rank(sc)
        for sid in cands:
            scores[sid].append(sc[sid])
            if r[sid] <= top_n:
                hits[sid] += 1
    rows = []
    for sid in cands:
        p = hits[sid] / runs
        s = sorted(scores[sid])
        if rb[sid] <= top_n or p >= 0.1:
            rows.append({"id": sid, "nom": sites[sid].get("nom"), "commune": sites[sid].get("commune"),
                         "usage": base[sid]["usage"], "rang_base": rb[sid], "score_base": base[sid]["g"]["global"],
                         "p_top": round(p, 2), "score_p10": round(s[int(0.1 * (runs - 1))], 1),
                         "score_p90": round(s[int(0.9 * (runs - 1))], 1),
                         "confiance": base[sid]["g"]["confiance"],
                         "robustesse": "robuste" if p >= 0.8 else "probable" if p >= 0.5 else "fragile" if p >= 0.2 else "improbable"})
    rows.sort(key=lambda x: (-x["p_top"], x["rang_base"]))
    robust = [r for r in rows if r["robustesse"] == "robuste"]
    synth = (f"Sur {runs} tirages, {len(robust)} site(s) restent dans le top {top_n} dans au moins 80 % des cas. "
             "Les sites « fragiles » ont un bon score moyen mais reposent sur des données incertaines : "
             "une étude ciblée lèverait l'incertitude avant d'engager un budget.")
    return {"lignes": rows, "runs": runs, "top_n": top_n, "synthese": synth}


def usages_list():
    return USAGES
