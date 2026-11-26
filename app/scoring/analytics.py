"""Vues agrégées : carte de chaleur des coûts cachés et horloge ZAN."""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from . import calendar
from . import config as C


# ------------------------------------------------------------ coûts cachés
def heatmap(ranked: dict[str, dict], sites: dict, level: str = "epci") -> dict:
    """`ranked` : sortie de stress.base_ranking (usage retenu par site)."""
    groups: dict[str, list] = {}
    points = []
    for sid, b in ranked.items():
        rec = sites[sid]
        key = rec.get(level) or "—"
        ur = b["ur"]
        cost = ur["cost"]
        mid = (cost["eur_m2_bas"] + cost["eur_m2_haut"]) / 2
        item = {"id": sid, "nom": rec.get("nom"), "commune": rec.get("commune"), "d2": ur["dims"]["d2"],
                "friction": ur["dims"]["d3"], "cout_m2": round(mid, 1), "conf": b["g"]["confiance"],
                "usage": b["usage"], "risque": cost["risque"], "global": b["g"]["global"],
                "surface_ha": round(rec.get("surface_m2", 0) / 10_000, 2)}
        groups.setdefault(key, []).append(item)
        points.append({**item, "lat": rec.get("lat"), "lon": rec.get("lon"), "groupe": key, "departement": rec.get("departement")})
    rows = []
    for key, items in groups.items():
        piegeux = [i for i in items if i["d2"] < C.PIEGEUX_D2_MAX]
        pepites = [i for i in items if i["d2"] >= C.PEPITE_D2_MIN and i["friction"] <= C.PEPITE_FRICTION_MAX
                   and i["conf"] >= C.PEPITE_CONF_MIN]
        rows.append({
            "groupe": key,
            "n": len(items),
            "surface_ha": round(sum(i["surface_ha"] for i in items), 1),
            "d2_moyen": round(statistics.mean(i["d2"] for i in items), 1),
            "cout_m2_median": round(statistics.median(i["cout_m2"] for i in items), 1),
            "part_piegeux": round(len(piegeux) / len(items), 2),
            "n_piegeux": len(piegeux),
            "n_pepites": len(pepites),
            "piegeux": sorted(piegeux, key=lambda i: i["d2"])[:3],
            "pepites": sorted(pepites, key=lambda i: -i["global"])[:3],
        })
    rows.sort(key=lambda r: -r["part_piegeux"])
    return {"niveau": level, "groupes": rows, "points": points,
            "seuils": {"piegeux_d2_max": C.PIEGEUX_D2_MAX, "pepite_d2_min": C.PEPITE_D2_MIN,
                       "pepite_friction_max": C.PEPITE_FRICTION_MAX, "pepite_conf_min": C.PEPITE_CONF_MIN}}


# ------------------------------------------------------------- horloge ZAN
# Mois nécessaires pour constituer un dossier de financement, selon l'avancement
STAGE_MONTHS = {None: 6.0, "reperee": 6.0, "pre_etudiee": 3.0, "en_etude": 1.0, "abandonnee": None}


def load_windows() -> dict:
    return calendar.load()


def _d(s: str) -> date:
    return date.fromisoformat(s)


def maturity(ur: dict, stage: str | None, today: date) -> dict:
    base = STAGE_MONTHS.get(stage, 8.0)
    if base is None:
        return {"mois": None, "date": None}
    extra = min(4.0, 0.2 * ur["friction"]["delai_additionnel_mois"])
    m = base + extra
    return {"mois": round(m, 1), "date": (today + timedelta(days=int(m * 30.4))).isoformat()}


def eligible(win: dict, usage: str, ur: dict, rec) -> tuple[bool, str]:
    if usage not in win["usages"]:
        return False, "usage non éligible"
    cond = win.get("conditions", {})
    if cond.get("bilan_deficitaire") and not (ur["market"]["bilan_net_bas"] < 0):
        return False, "bilan non déficitaire"
    if cond.get("pollue") and not (rec.get("basol") or rec.get("sis") or (rec.get("basias_count") or 0) > 0
                                   or rec.get("pollution_constatee") in ("moderee", "forte")):
        return False, "site non pollué"
    if cond.get("surface_min_ha") and rec.get("surface_m2", 0) / 10_000 < cond["surface_min_ha"]:
        return False, f"surface < {cond['surface_min_ha']} ha"
    if rec.get("site_extension"):
        return False, "site en extension"
    return True, ""


def zan_clock(ranked: dict[str, dict], sites: dict, stages: dict[str, str], today: date | None = None,
              top: int = 60) -> dict:
    today = today or date.today()
    data = load_windows()
    wins = [w for w in data["fenetres"] if _d(w["cloture"]) >= today]
    order = sorted(ranked, key=lambda k: -ranked[k]["g"]["global"])[:top]
    matches = []
    for sid in order:
        b = ranked[sid]
        rec = sites[sid]
        stage = stages.get(sid)
        mat = maturity(b["ur"], stage, today)
        if mat["date"] is None:
            continue
        ready = _d(mat["date"])
        for w in wins:
            ok, _ = eligible(w, b["usage"], b["ur"], rec)
            if not ok:
                continue
            close = _d(w["cloture"])
            if ready <= close:
                statut = "coïncide"
            elif ready <= close + timedelta(days=60):
                statut = "trop juste"
            else:
                continue
            matches.append({"id": sid, "nom": rec.get("nom"), "commune": rec.get("commune"), "usage": b["usage"],
                            "score": b["g"]["global"], "stage": stage, "maturite": mat, "fenetre": w["id"], "fenetre_type": w.get("type"), "fenetre_statut": w.get("statut_verification"),
                            "fenetre_nom": w["nom"], "cloture": w["cloture"], "statut": statut,
                            "marge_jours": (close - ready).days, "maturite_requise": w.get("maturite_requise")})
    matches.sort(key=lambda m: (m["statut"] != "coïncide", m["marge_jours"]))
    jalons = []
    for e in data["echeances_reglementaires"]:
        d = _d(e["date"])
        jalons.append({**e, "passee": d < today, "jours_restants": (d - today).days})
    return {"aujourdhui": today.isoformat(), "fenetres": wins, "jalons": jalons, "correspondances": matches,
            "types": data.get("types", {}), "avertissement": data.get("_avertissement")}
