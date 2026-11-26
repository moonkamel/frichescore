"""Moteur FricheScore : assemble les cinq dimensions, la confiance et l'explication.

Règles imposées par la conception produit :
  * jamais de score global sans les cinq notes et les trois facteurs déterminants ;
  * refus de scorer une friche sans localisation, surface et au moins une
    information sur la pollution ou l'usage passé (« non évaluable ») ;
  * pénalité explicite quand le score repose sur des données anciennes ou fragiles ;
  * recommandation d'études complémentaires plutôt que fausse précision.
"""
from __future__ import annotations

from datetime import date

from . import config as C
from .cost import compute_cost
from .friction import compute_friction
from .impact import compute_impact
from .indicators import label
from .market import compute_market
from .record import Record, Tracker
from .usages import SCORERS

POLLUTION_OR_PAST_KEYS = ("basol", "basias_count", "icpe_count", "sis", "pollution_constatee", "activite_passee")


# --------------------------------------------------------------- évaluabilité
def check_evaluable(rec: Record) -> list[str]:
    missing = []
    if not ((rec.has("lat") and rec.has("lon")) or rec.has("code_insee")):
        missing.append("localisation (coordonnées ou code INSEE)")
    if not rec.has("surface_m2") or float(rec.get("surface_m2", 0)) <= 0:
        missing.append("surface du site")
    has_pol = any(rec.has(k) for k in POLLUTION_OR_PAST_KEYS)
    has_type = rec.get("type_friche", "inconnu") not in ("inconnu", None)
    if not (has_pol or has_type):
        missing.append("au moins une source de pollution (BASOL, BASIAS, ICPE, SIS, diagnostic) ou l'usage passé")
    return missing


# -------------------------------------------------------------- une évaluation
def _top(contribs: list[dict], n: int = 6) -> list[dict]:
    return sorted(contribs, key=lambda c: -abs(c["delta"]))[:n]


def _d1_phrase(usage: str, score: float, contribs: list[dict]) -> str:
    lvl = "élevé" if score >= 65 else "moyen" if score >= 45 else "faible"
    pos = [c["label"] for c in sorted(contribs, key=lambda c: -c["delta"]) if c["delta"] > 0][:2]
    neg = [c["label"] for c in sorted(contribs, key=lambda c: c["delta"]) if c["delta"] < 0][:1]
    parts = []
    if pos:
        parts.append(", ".join(p.lower() for p in pos))
    if neg:
        parts.append("mais " + neg[0].lower())
    return f"Potentiel {C.USAGES[usage].lower()} {lvl}" + (" : " + " ; ".join(parts) + "." if parts else ".")


def evaluate_usage(rec: Record, usage: str, hyp: dict | None = None, today: date | None = None) -> dict:
    hyp = hyp or {}
    t1, t2, t3, t4, t5 = (Tracker(rec, today) for _ in range(5))
    d1, blockers = SCORERS[usage](t1)
    cost = compute_cost(t2, usage, hyp)
    fr = compute_friction(t3, usage, hyp)
    mk = compute_market(t4, usage, cost, hyp)
    im = compute_impact(t5, usage, mk, hyp)

    dims = {"d1": d1, "d2": cost["score"], "d3": fr["friction"], "d4": mk["score"], "d5": im["score"]}
    confs, weak = {}, {}
    for k, t in zip(("d1", "d2", "d3", "d4", "d5"), (t1, t2, t3, t4, t5)):
        c, w = t.confidence()
        confs[k] = round(c, 3)
        weak[k] = w
    contribs = {k: _top(t.contribs) for k, t in zip(("d1", "d2", "d3", "d4", "d5"), (t1, t2, t3, t4, t5))}
    alerts = t1.alerts + t2.alerts + t3.alerts + t4.alerts + t5.alerts
    return {
        "usage": usage,
        "dims": {k: round(v, 1) for k, v in dims.items()},
        "conf": confs,
        "weak": weak,
        "contribs": contribs,
        "blockers": blockers,
        "alerts": alerts,
        "phrases": {
            "d1": _d1_phrase(usage, d1, t1.contribs),
            "d2": cost["phrase"],
            "d3": fr["phrase"],
            "d4": mk["phrase"],
            "d5": im["phrase"],
        },
        "cost": cost,
        "friction": fr,
        "market": mk,
        "impact": im,
    }


# ---------------------------------------------------------------- score global
def normalize_weights(weights: dict | None, d5_enabled: bool) -> dict:
    w = dict(C.PROFILES["equilibre"]["weights"])
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    if not d5_enabled:
        w["d5"] = 0.0
    if sum(w.values()) <= 0:
        w = {k: 1.0 for k in w}
    return w


def global_score(ur: dict, weights: dict, d5_enabled: bool = True) -> dict:
    """Combinaison pondérée. La friction (d3) entre sous la forme 100 - friction.
    Formule dupliquée côté navigateur (frontend/app.js : globalScore) pour un
    recalcul instantané quand l'utilisateur déplace les curseurs."""
    w = normalize_weights(weights, d5_enabled)
    tot = sum(w.values())
    d = ur["dims"]
    vals = {"d1": d["d1"], "d2": d["d2"], "d3": 100 - d["d3"], "d4": d["d4"], "d5": d["d5"]}
    raw = sum(w[k] * vals[k] for k in w) / tot
    conf = sum(w[k] * ur["conf"][k] for k in w) / tot
    penalty = round((1 - conf) * C.CONFIDENCE_MAX_PENALTY, 1)
    g = raw - penalty
    blocked = bool(ur["blockers"])
    if blocked:
        g = min(g, C.BLOCKER_CAP)

    effects = []
    for k in w:
        if w[k] == 0:
            continue
        for c in ur["contribs"][k]:
            effects.append({"label": c["label"], "dim": k, "effet": round(c["delta"] * w[k] / tot, 1), "key": c.get("key")})
    if penalty >= 1:
        weakest = []
        for k in w:
            if w[k]:
                weakest += ur["weak"][k]
        weakest = sorted({x["key"]: x for x in weakest}.values(), key=lambda x: x["confiance"])[:1]
        def _fmt(x):
            etat = "manquant" if x["manquant"] else "confiance {:.2f}".format(x["confiance"]).replace(".", ",")
            lb = label(x["key"])
            return f"{lb[0].lower() + lb[1:]} ({etat})"
        lbl = "Données fragiles : " + ", ".join(_fmt(x) for x in weakest)
        effects.append({"label": lbl, "dim": "conf", "effet": -penalty, "key": None})
    drivers = sorted(effects, key=lambda e: -abs(e["effet"]))[:3]
    return {
        "global": round(max(0.0, g), 1),
        "brut": round(raw, 1),
        "confiance": round(conf, 3),
        "penalite": penalty,
        "bloque": blocked,
        "drivers": drivers,
    }


def confidence_label(c: float) -> str:
    return "élevé" if c >= 0.8 else "moyen" if c >= 0.6 else "faible"


# -------------------------------------------------------- recommandations
def recommendations(rec: Record, ur: dict | None = None) -> list[dict]:
    r = []
    has_pol_db = any(rec.has(k) for k in ("basol", "basias_count", "icpe_count", "sis"))
    if not rec.has("pollution_constatee"):
        if rec.get("basol") or rec.get("sis"):
            msg = "Diagnostic de pollution des sols et des eaux souterraines, puis plan de gestion (norme NF X31-620)"
            r.append({"priorite": "haute", "etude": msg, "motif": "Site BASOL/SIS : coût de dépollution très dépendant du diagnostic"})
        elif (rec.get("basias_count") or 0) > 0 or (rec.get("icpe_count") or 0) > 0:
            r.append({"priorite": "haute", "etude": "Étude historique et documentaire, puis diagnostic de sol ciblé (NF X31-620)",
                      "motif": "Activités passées potentiellement polluantes non diagnostiquées (BASIAS / ICPE)"})
        elif not has_pol_db:
            r.append({"priorite": "haute", "etude": "Interrogation des bases BASOL / BASIAS / ICPE / SIS",
                      "motif": "Aucune base de pollution renseignée pour ce site"})
    if rec.get("bati_emprise_m2", 0) > 0:
        a = rec.get("bati_annee")
        if a is None or a < 1997:
            r.append({"priorite": "moyenne", "etude": "Repérage amiante et plomb avant démolition",
                      "motif": "Bâti antérieur à 1997 ou d'année inconnue"})
        if rec.get("bati_etat", "inconnu") == "inconnu":
            r.append({"priorite": "moyenne", "etude": "Visite de site et diagnostic structurel du bâti",
                      "motif": "État du bâti inconnu : arbitrage réemploi / démolition impossible"})
    if rec.get("zone_humide") == "probable" or not rec.has("zone_humide"):
        r.append({"priorite": "moyenne", "etude": "Délimitation des zones humides (critères sol et végétation)",
                  "motif": "Présence de zone humide probable ou non vérifiée"})
    if not rec.has("nb_proprietaires"):
        r.append({"priorite": "moyenne", "etude": "Étude foncière (fichiers fonciers, relevé de propriété)",
                  "motif": "Structure de propriété inconnue"})
    if not rec.has("plu_zone") or rec.get("plu_zone") == "inconnu":
        r.append({"priorite": "moyenne", "etude": "Analyse réglementaire du PLU(i) et des servitudes",
                  "motif": "Zonage d'urbanisme non renseigné"})
    if ur and ur["market"]["marchand"] and ur["market"]["bilan_net_bas"] < 0 < ur["market"]["bilan_net_haut"]:
        r.append({"priorite": "basse", "etude": "Étude de marché et pré-bilan d'aménagement",
                  "motif": "Bilan d'opération à cheval sur l'équilibre"})
    if rec.get("ppri") in ("rouge", "bleu"):
        r.append({"priorite": "moyenne", "etude": "Consultation de la DDT(M) sur le règlement du PPRi",
                  "motif": f"Site en zone {rec.get('ppri')} du PPRi"})
    return r


# ---------------------------------------------------------------- site complet
def evaluate_site(rec: Record, hyp: dict | None = None, usages: list[str] | None = None,
                  today: date | None = None) -> dict:
    missing = check_evaluable(rec)
    if missing:
        return {"evaluable": False, "manquants": missing}
    us = usages or list(C.USAGES)
    res = {u: evaluate_usage(rec, u, hyp, today) for u in us}
    return {"evaluable": True, "usages": res}


def best_usage(site_eval: dict, weights: dict, d5_enabled: bool = True) -> tuple[str, dict]:
    best = None
    for u, ur in site_eval["usages"].items():
        g = global_score(ur, weights, d5_enabled)
        key = (not g["bloque"], g["global"])
        if best is None or key > best[0]:
            best = (key, u, g)
    return best[1], best[2]
