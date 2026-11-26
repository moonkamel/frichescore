"""« Jumeau de scénario » : comparaison argumentée de deux sites ou deux usages.

Un des deux termes peut être un site en EXTENSION (terrain non artificialisé en
périphérie) : l'outil montre alors ce que la friche coûte de plus en
remise en état et ce qu'elle rapporte en friction évitée et en budget ZAN préservé.
"""
from __future__ import annotations

from datetime import date

from .config import DIMENSIONS, USAGES
from .engine import evaluate_usage, global_score
from .record import Provenance, Record


def extension_site(params: dict, commune: Record | None) -> Record:
    """Construit un site virtuel en extension urbaine à partir de quelques paramètres."""
    r = Record()
    if commune is not None:
        for k, v in commune.values.items():
            if not k.startswith("_"):
                r.values[k] = v
                if k in commune.prov:
                    r.prov[k] = commune.prov[k]
    h = Provenance("Hypothèse utilisateur — site en extension", date.today().isoformat(), "moyen")
    r.set("id", "EXTENSION")
    r.set("nom", params.get("nom") or "Site en extension (hypothèse)", h)
    r.set("site_extension", True, h)
    r.set("surface_m2", float(params.get("surface_m2", 30000)), h)
    r.set("type_friche", "agricole", h)
    r.set("artificialise", False, h)
    r.set("bati_emprise_m2", 0, h)
    r.set("basol", False, h)
    r.set("basias_count", 0, h)
    r.set("icpe_count", 0, h)
    r.set("pollution_constatee", "aucune", Provenance("Hypothèse : terrain agricole non pollué", date.today().isoformat(), "moyen"))
    r.set("nb_proprietaires", int(params.get("nb_proprietaires", 2)), h)
    r.set("proprietaire_type", "prive", h)
    r.set("plu_zone", params.get("plu_zone", "AU"), h)
    r.set("plu_vocation", params.get("plu_vocation", "mixte"), h)
    r.set("zone_humide", params.get("zone_humide", "non"), h)
    r.set("distance_centre_km", float(params.get("distance_centre_km", 4)), h)
    r.set("distance_gare_km", float(params.get("distance_gare_km", 5)), h)
    r.set("desserte_tc", params.get("desserte_tc", "faible"), h)
    r.set("distance_autoroute_km", float(params.get("distance_autoroute_km", 3)), h)
    r.set("distance_poste_source_km", float(params.get("distance_poste_source_km", 8)), h)
    r.set("forme", "compacte", h)
    r.set("prix_terrain_friche_m2", float(params.get("prix_terrain_m2", 12)), h)
    r.set("captage", "non", h)
    r.set("ppri", "non", h)
    r.set("servitudes_count", 0, h)
    for k in ("pprt", "natura2000", "znieff", "abf", "enclave", "sis"):
        r.set(k, bool(params.get(k, False)), h)
    r.set("acces", params.get("acces", "bon"), h)
    if params.get("code_insee"):
        r.set("code_insee", params["code_insee"])
    return r


def compare(a: tuple[Record, str], b: tuple[Record, str], weights: dict, d5: bool = True,
            today: date | None = None) -> dict:
    out = []
    for rec, usage in (a, b):
        ur = evaluate_usage(rec, usage, None, today)
        g = global_score(ur, weights, d5)
        out.append({"id": rec.get("id"), "nom": rec.get("nom"), "commune": rec.get("commune"),
                    "usage": usage, "usage_label": USAGES[usage], "dims": ur["dims"], "global": g,
                    "phrases": ur["phrases"], "cost": ur["cost"], "friction": ur["friction"],
                    "market": ur["market"], "impact": ur["impact"], "blockers": ur["blockers"],
                    "conf": ur["conf"], "contribs": ur["contribs"]})
    A, B = out
    ga, gb = A["global"]["global"], B["global"]["global"]
    win, lose = (A, B) if ga >= gb else (B, A)
    margin = abs(ga - gb)

    diffs = []
    for d in ("d1", "d2", "d3", "d4", "d5"):
        w = float(weights.get(d, 0)) if (d != "d5" or d5) else 0
        va, vb = win["dims"][d], lose["dims"][d]
        adv = (va - vb) if d != "d3" else (vb - va)
        diffs.append((adv * w, d, va, vb))
    diffs.sort(reverse=True)
    pos = [x for x in diffs if x[0] > 0][:2]

    l1 = (f"{win['nom']} ({win['usage_label'].lower()}) est préférable : {max(ga, gb):.0f} contre {min(ga, gb):.0f} "
          f"(+{margin:.0f} pts).")
    if pos:
        l2 = "Ce qui fait la différence : " + " ; ".join(
            f"{DIMENSIONS[d].lower()} ({va:.0f} vs {vb:.0f})" for _, d, va, vb in pos) + "."
    else:
        l2 = "Aucune dimension ne départage nettement les deux sites."
    neg = sorted([c for k in ("d1", "d2", "d3", "d4") for c in win["contribs"][k] if c["delta"] < 0],
                 key=lambda c: c["delta"])
    cw, cl = win["global"]["confiance"], lose["global"]["confiance"]
    if margin < 5 or margin < (1 - min(cw, cl)) * 20:
        l3 = (f"Écart non significatif au regard de l'incertitude des données (confiance {cw:.2f} vs {cl:.2f}) : "
              "départager les sites par une étude complémentaire avant décision.")
    elif win["blockers"]:
        l3 = "Attention : le site retenu présente un motif rédhibitoire — " + win["blockers"][0] + "."
    elif neg:
        l3 = f"Point de vigilance : {neg[0]['label'].lower()} (confiance des données {cw:.2f})."
    else:
        l3 = f"Recommandation solide (confiance des données {cw:.2f})."
    if lose.get("id") == "EXTENSION" or win.get("id") == "EXTENSION":
        ext = A if A.get("id") == "EXTENSION" else B
        ha = (a[0] if ext is A else b[0]).get("surface_m2", 0) / 10_000
        l3 += (f" Le site en extension consommerait {ha:.1f} ha d'espaces naturels, agricoles ou forestiers, "
               "décomptés du budget ZAN de la commune.")
    return {"a": A, "b": B, "gagnant": win["id"], "ecart": round(margin, 1), "synthese": [l1, l2, l3]}
