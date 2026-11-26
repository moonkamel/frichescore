"""Quelle étude lancer en premier ? (valeur de l'information)

Pour chaque site proche du seuil de décision (le top N du classement) et chaque étude préalable possible,
on simule les résultats plausibles de l'étude (avec des probabilités a priori explicites), on recalcule
le FricheScore du site pour chacun, et on mesure la probabilité que l'étude CHANGE LA DÉCISION :
faire entrer dans le top N un site qui n'y est pas, ou en faire sortir un site qui y est.

Une étude dont aucun résultat ne peut changer la décision n'apporte rien au classement (elle peut rester
utile pour d'autres raisons). Rapportée à son coût, cette probabilité permet de répartir un budget d'études.

Les probabilités a priori sont des hypothèses de travail, affichées à l'utilisateur ; elles dépendent des
signaux déjà connus (un site BASOL a plus de chances de révéler une pollution forte qu'un site sans signal).
"""
from __future__ import annotations

from datetime import date

from . import config as C
from .engine import best_usage, evaluate_site, global_score
from .record import Provenance, Record

SRC = "Résultat simulé d'étude"


def _outcomes(o: list[tuple[float, str, dict]]):
    tot = sum(p for p, _, _ in o)
    return [(p / tot, lbl, vals) for p, lbl, vals in o]


def etudes_possibles(rec: Record) -> list[dict]:
    """Études applicables au site, avec leurs résultats possibles et leurs probabilités a priori."""
    out = []
    if not rec.has("pollution_constatee"):
        if rec.get("basol") or rec.get("sis"):
            pri, cle, lbl = (0.15, 0.45, 0.40), "diag_sol_basol", "Diagnostic de pollution sols et eaux (site BASOL/SIS)"
            base = "site BASOL/SIS"
        elif (rec.get("basias_count") or 0) > 0 or (rec.get("icpe_count") or 0) > 0:
            pri, cle, lbl = (0.40, 0.40, 0.20), "diag_sol", "Étude historique et diagnostic de sol ciblé"
            base = "activités passées BASIAS/ICPE"
        else:
            pri, cle, lbl = (0.65, 0.28, 0.07), "diag_sol_leger", "Étude historique et documentaire des sols"
            base = "aucun signal dans les bases"
        out.append({"cle": cle, "etude": lbl, "indicateur": "pollution_constatee",
                    "hypothese": f"a priori ({base}) : aucune {pri[0]:.0%}, modérée {pri[1]:.0%}, forte {pri[2]:.0%}",
                    "resultats": _outcomes([(pri[0], "pas de pollution", {"pollution_constatee": "aucune"}),
                                            (pri[1], "pollution modérée", {"pollution_constatee": "moderee"}),
                                            (pri[2], "pollution forte", {"pollution_constatee": "forte"})])})
    if (rec.get("bati_emprise_m2") or 0) > 0 and rec.get("bati_etat", "inconnu") == "inconnu":
        out.append({"cle": "visite_bati", "etude": "Visite de site et diagnostic du bâti", "indicateur": "bati_etat",
                    "hypothese": "a priori : bon 20 %, moyen 35 %, dégradé 30 %, ruine 15 %",
                    "resultats": _outcomes([(0.20, "bâti en bon état", {"bati_etat": "bon"}),
                                            (0.35, "bâti moyen", {"bati_etat": "moyen"}),
                                            (0.30, "bâti dégradé", {"bati_etat": "degrade"}),
                                            (0.15, "bâti en ruine", {"bati_etat": "ruine"})])})
    zh = rec.get("zone_humide")
    if zh == "probable" or not rec.has("zone_humide"):
        p = 0.45 if zh == "probable" else 0.10
        out.append({"cle": "zone_humide", "etude": "Délimitation des zones humides", "indicateur": "zone_humide",
                    "hypothese": f"a priori ({'zone humide probable' if zh == 'probable' else 'non vérifiée'}) : avérée {p:.0%}",
                    "resultats": _outcomes([(p, "zone humide avérée", {"zone_humide": "avere"}),
                                            (1 - p, "pas de zone humide", {"zone_humide": "non"})])})
    if not rec.has("nb_proprietaires"):
        out.append({"cle": "foncier", "etude": "Étude foncière (relevé de propriété)", "indicateur": "nb_proprietaires",
                    "hypothese": "a priori : 1 propriétaire 55 %, 3 30 %, 8 15 %",
                    "resultats": _outcomes([(0.55, "propriétaire unique", {"nb_proprietaires": 1}),
                                            (0.30, "3 propriétaires", {"nb_proprietaires": 3}),
                                            (0.15, "8 propriétaires", {"nb_proprietaires": 8})])})
    if not rec.has("plu_zone") or rec.get("plu_zone") == "inconnu":
        out.append({"cle": "plu", "etude": "Analyse réglementaire du PLU(i)", "indicateur": "plu_zone",
                    "hypothese": "a priori : zone U 60 %, AU 15 %, A 10 %, N 15 %",
                    "resultats": _outcomes([(0.60, "zone U", {"plu_zone": "U"}), (0.15, "zone AU", {"plu_zone": "AU"}),
                                            (0.10, "zone A", {"plu_zone": "A"}), (0.15, "zone N", {"plu_zone": "N"})])})
    if rec.get("risque_inondation") and not rec.has("ppri"):
        out.append({"cle": "ppri", "etude": "Vérification du zonage PPRi", "indicateur": "ppri",
                    "hypothese": "a priori (risque recensé) : hors zonage 50 %, bleu 35 %, rouge 15 %",
                    "resultats": _outcomes([(0.50, "hors zone réglementée", {"ppri": "non"}),
                                            (0.35, "zone bleue", {"ppri": "bleu"}), (0.15, "zone rouge", {"ppri": "rouge"})])})
    for e in out:
        lo, hi = C.ETUDES_COUT_KEUR[e["cle"]]
        e["cout_keur"] = [lo, hi]
    return out


def _score(rec: Record, usage: str | None, weights: dict, d5: bool, today) -> tuple[float, str] | None:
    ev = evaluate_site(rec, usages=[usage] if usage else None, today=today)
    if not ev["evaluable"]:
        return None
    if usage:
        return global_score(ev["usages"][usage], weights, d5)["global"], usage
    u, g = best_usage(ev, weights, d5)
    return g["global"], u


def valeur_information(sites: dict[str, Record], ranked: dict[str, dict], usage: str | None, weights: dict, d5: bool,
                       top_n: int = 10, budget_keur: float | None = None, marge: float = 20.0,
                       today: date | None = None) -> dict:
    """`ranked` = base_ranking(...) déjà calculé. Retourne les études classées et une sélection sous budget."""
    today = today or date.today()
    scores = {sid: x["g"]["global"] for sid, x in ranked.items()}
    order = sorted(scores, key=lambda k: -scores[k])
    if len(order) <= top_n:
        return {"etudes": [], "selection": [], "top_n": top_n, "seuil": None,
                "message": f"Le périmètre compte {len(order)} site(s) évaluable(s) : tous sont dans le top {top_n}, "
                           "aucune étude ne peut changer la sélection. Réduisez le top N ou élargissez le périmètre."}
    seuil = (scores[order[top_n - 1]] + scores[order[top_n]]) / 2
    rows = []
    for rang, sid in enumerate(order, 1):
        g0 = scores[sid]
        if abs(g0 - seuil) > marge:
            continue
        others = sorted((v for k, v in scores.items() if k != sid), reverse=True)
        barre = others[top_n - 1]  # score à dépasser pour être dans le top N
        dedans = rang <= top_n
        rec = sites[sid]
        for e in etudes_possibles(rec):
            res, p_change, esp = [], 0.0, 0.0
            for p, lbl, vals in e["resultats"]:
                r2 = rec.copy()
                for k, v in vals.items():
                    r2.set(k, v, Provenance(SRC, today.isoformat(), "eleve"))
                sc = _score(r2, usage, weights, d5, today)
                if sc is None:
                    continue
                g1, u1 = sc
                dedans1 = g1 > barre or (g1 == barre and dedans)
                change = dedans1 != dedans
                p_change += p * change
                esp += p * (g1 - g0)
                res.append({"resultat": lbl, "probabilite": round(p, 2), "score": round(g1, 1), "usage": u1,
                            "change_decision": change})
            if not res:
                continue
            cout = sum(e["cout_keur"]) / 2
            rows.append({"site": sid, "nom": rec.get("nom"), "commune": rec.get("commune"), "rang": rang,
                         "score": round(g0, 1), "usage": ranked[sid]["usage"], "dans_top": dedans,
                         "etude": e["etude"], "cle": e["cle"], "indicateur": e["indicateur"], "hypothese": e["hypothese"],
                         "cout_keur": e["cout_keur"], "p_changement": round(p_change, 3),
                         "ecart_attendu": round(esp, 1),
                         "fourchette_score": [min(x["score"] for x in res), max(x["score"] for x in res)],
                         "efficacite": round(p_change / cout, 4) if cout else 0.0, "resultats": res})
    rows.sort(key=lambda r: (-r["p_changement"], -r["efficacite"], r["rang"]))
    selection, total = [], 0.0
    if budget_keur:
        for r in sorted((r for r in rows if r["p_changement"] > 0), key=lambda r: -r["efficacite"]):
            c = sum(r["cout_keur"]) / 2
            if total + c <= budget_keur:
                selection.append({"site": r["site"], "nom": r["nom"], "etude": r["etude"], "cout_keur": r["cout_keur"],
                                  "p_changement": r["p_changement"]})
                total += c
    attendu = sum(s["p_changement"] for s in selection)
    return {"etudes": rows, "top_n": top_n, "seuil": round(seuil, 1), "sites_examines": len({r["site"] for r in rows}),
            "selection": selection, "budget_keur": budget_keur, "cout_selection_keur": round(total, 1),
            "decisions_changees_attendues": round(attendu, 2)}
