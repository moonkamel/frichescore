"""Orchestration : chargement des périmètres, calculs, portfolio, exports."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date

from . import db
from .demo.generate import generate
from .ingest import connectors
from .reports import pdf
from .scoring import calendar, params
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
    from . import tenant
    db.init()
    if db.is_empty() and not tenant.auth_enabled():  # en ligne, pas de données fictives mêlées à celles des clients
        load_demo()
    apply_params()


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
    sig = db.signal_counts()
    out = []
    for sid, rec in sites.items():
        meta = {"id": sid, "nom": rec.get("nom"), "commune": rec.get("commune"), "code_insee": rec.get("code_insee"),
                "departement": rec.get("departement"), "epci": rec.get("epci"), "lat": rec.get("lat"), "lon": rec.get("lon"),
                "surface_m2": rec.get("surface_m2"), "type_friche": rec.get("type_friche"),
                "statut": rec.get("statut_cartofriches"), "origine": rec.get("_origine"),
                "portfolio": port.get(sid, {}).get("stage"), "signaux": sig.get(sid)}
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
            "recommandations": recommendations(rec, ur), "zan": z, "weights": w,
            "note_ia": db.ia_note_latest(sid, usage), "signaux": db.signals_list(sid, limit=30)}


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


def etudes(perimeter, usage, weights, d5, top_n: int = 10, budget_keur: float | None = None) -> dict:
    """Quelle étude lancer en premier : probabilité qu'une étude change la sélection du top N."""
    from .scoring.etudes import valeur_information
    sites = db.load_sites(perimeter)
    w = _w(weights, d5)
    ranked = base_ranking(sites, usage, w, d5)
    stages = {k: v["stage"] for k, v in db.portfolio().items()}
    ranked = {k: v for k, v in ranked.items() if stages.get(k) != "abandonnee"}
    return valeur_information(sites, ranked, usage, w, d5, top_n, budget_keur)


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


def ensure_visible(sid: str) -> None:
    """Refuse d'écrire sur une friche que l'organisme courant ne voit pas."""
    if sid not in db.friche_origins():
        raise KeyError(sid)


def portfolio_set(sid, stage, usage, notes, weights, d5):
    ensure_visible(sid)
    if stage not in STAGES:
        raise ValueError("étape inconnue")
    prev = db.portfolio().get(sid)
    db.set_portfolio(sid, stage, usage, notes)
    if prev is None:
        _snapshot(sid, "Ajout au portfolio", weights, d5)
    elif prev["stage"] != stage:
        _snapshot(sid, f"Étape : {STAGES.get(prev['stage'], prev['stage'])} → {STAGES[stage]}", weights, d5)


def field_add(sid, key, value, source, date_, confiance, auteur, weights, d5):
    ensure_visible(sid)
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
           "demo": any(str(r.get("_origine", "")).startswith("demo") for r in sites.values()),
           "parametres_modifies": [{"label": params.SCHEMA_BY_PATH[p_]["label"], "groupe": params.SCHEMA_BY_PATH[p_]["groupe"],
                                    "valeur": o["value"], "defaut": params.default(p_), "note": o.get("note")}
                                   for p_, o in db.param_overrides().items() if p_ in params.SCHEMA_BY_PATH]}
    return pdf.report_pdf(ctx)


# ---------------------------------------------------------------- import
REQUIRED_HINT = {"code_insee": "code INSEE de la commune", "surface_m2": "surface du site (m²)"}


def decode_bytes(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        first = text.splitlines()[0] if text else ""
        return max(";,\t|", key=first.count) if first else ";"


def import_csv(text: str, origine_label: str, default_date: str | None, dry_run: bool = False,
               encoding: str = "utf-8") -> dict:
    """Import tolérant : chaque problème est rapporté (ligne, colonne, message), les cellules
    invalides sont ignorées, une ligne n'est rejetée que si elle n'est rattachable à aucun territoire."""
    from .ingest.mapping import resolve_column, validate_row

    report = {"mode": "vérification" if dry_run else "import", "encodage": encoding, "separateur": None,
              "lignes_lues": 0, "importees": 0, "rejetees": 0, "avec_avertissements": 0, "evaluables": 0,
              "colonnes_reconnues": {}, "colonnes_ignorees": [], "erreurs_fichier": [], "problemes": [],
              "problemes_tronques": 0}
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        report["erreurs_fichier"].append("Le fichier est vide.")
        return report
    delim = sniff_delimiter(text)
    report["separateur"] = {";": "point-virgule", ",": "virgule", "\t": "tabulation", "|": "barre verticale"}.get(delim, delim)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    if len(headers) <= 1:
        report["erreurs_fichier"].append(
            "Une seule colonne détectée : le séparateur n'est probablement pas reconnu (attendus : ; , tabulation |).")
    mapping = {h: resolve_column(h) for h in headers if not h.strip().endswith(("__source", "__date", "__confiance"))}
    report["colonnes_reconnues"] = {h: k for h, k in mapping.items() if k}
    report["colonnes_ignorees"] = sorted(h for h, k in mapping.items() if not k)
    keys = set(report["colonnes_reconnues"].values())
    if "code_insee" not in keys and not {"lat", "lon"} <= keys:
        report["erreurs_fichier"].append("Colonne obligatoire absente : code INSEE (code_insee) ou coordonnées (lat et lon). "
                                         "Aucune ligne ne peut être rattachée à un territoire.")
    if "surface_m2" not in keys:
        report["erreurs_fichier"].append("Colonne « surface_m2 » absente : les friches seront importées mais non évaluables.")
    if not keys & {"basol", "basias_count", "icpe_count", "sis", "pollution_constatee", "activite_passee", "type_friche"}:
        report["erreurs_fichier"].append("Aucune colonne de pollution ni d'usage passé (basol, basias_count, icpe_count, "
                                         "type_friche…) : les friches seront non évaluables.")
    slug = "import:" + (origine_label or "csv")
    seen_ids: dict[str, int] = {}
    MAXP = 500

    def issue(line, col, niveau, msg, val=""):
        if len(report["problemes"]) < MAXP:
            report["problemes"].append({"ligne": line, "colonne": col, "valeur": val, "niveau": niveau, "message": msg})
        else:
            report["problemes_tronques"] += 1

    for i, row in enumerate(reader, 2):  # ligne 1 = en-têtes
        if row is None or all((v or "").strip() == "" for v in row.values() if isinstance(v, str)):
            continue
        report["lignes_lues"] += 1
        if None in row:
            issue(i, "—", "avertissement", f"{len(row[None])} valeur(s) en trop par rapport aux en-têtes (séparateur dans une cellule ?)")
        rec, _ign, probs = validate_row(row, origine_label or "import CSV", default_date)
        for pb in probs:
            issue(i, pb["colonne"], pb["niveau"], pb["message"], pb["valeur"])
        if not rec.has("code_insee") and not (rec.has("lat") and rec.has("lon")):
            issue(i, "code_insee", "erreur", "ligne rejetée : ni code INSEE valide, ni coordonnées")
            report["rejetees"] += 1
            continue
        if not rec.has("id"):
            rec.values["id"] = f"IMP-{rec.get('code_insee', 'X')}-{i:05d}"
        rid = str(rec.get("id"))
        if rid in seen_ids:
            issue(i, "id", "avertissement", f"identifiant « {rid} » déjà présent ligne {seen_ids[rid]} : cette ligne le remplace")
        seen_ids[rid] = i
        if not rec.has("nom"):
            rec.values["nom"] = f"Friche {rid}"
        if any(pb["niveau"] == "avertissement" or pb["niveau"] == "erreur" for pb in probs):
            report["avec_avertissements"] += 1
        if not check_evaluable(rec):
            report["evaluables"] += 1
        if not dry_run:
            db.upsert_friche(rec, slug)
        report["importees"] += 1
    if dry_run:
        report["importables"] = report.pop("importees")
    return report


def csv_template() -> str:
    """Modèle d'import : en-têtes = clés du catalogue, deux lignes d'exemple réalistes."""
    cols = ["id"] + list(CATALOG)
    ex1 = {"id": "EX-001", "nom": "Ancienne fonderie (exemple)", "commune": "Roubaix", "code_insee": "59512", "departement": "59",
           "epci": "Métropole Européenne de Lille", "lat": "50,69", "lon": "3,17", "surface_m2": "25000", "type_friche": "industrielle",
           "statut_cartofriches": "verifiee", "activite_passee": "fonderie", "bati_emprise_m2": "8000", "bati_etat": "degrade",
           "bati_annee": "1965", "nb_proprietaires": "2", "proprietaire_type": "prive", "basol": "oui",
           "basol_milieux": "sols|eaux_souterraines", "basias_count": "2", "icpe_count": "1", "icpe_regime": "autorisation",
           "icpe_statut": "cessee", "zone_humide": "non", "ppri": "non", "plu_zone": "U", "plu_vocation": "activite"}
    ex2 = {"id": "EX-002", "nom": "Délaissé ferroviaire (exemple)", "commune": "Douai", "code_insee": "59178", "lat": "50,37",
           "lon": "3,08", "surface_m2": "12000", "type_friche": "ferroviaire", "basias_count": "0", "basol": "non",
           "icpe_count": "0", "proprietaire_type": "public", "nb_proprietaires": "1"}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for ex in (ex1, ex2):
        w.writerow([ex.get(c, "") for c in cols])
    return buf.getvalue()


def csv_dictionary() -> str:
    """Dictionnaire des colonnes du modèle (à ouvrir à côté du modèle dans un tableur)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["colonne", "libellé", "type", "valeurs acceptées", "obligatoire", "source de référence", "confiance par défaut"])
    w.writerow(["id", "Identifiant unique du site", "texte", "", "recommandé (généré sinon)", "", ""])
    for k, (lbl, typ, src, conf, vals) in CATALOG.items():
        oblig = ("oui (ou lat + lon)" if k == "code_insee" else "oui pour être évaluable" if k == "surface_m2"
                 else "au moins une de ces colonnes pour être évaluable" if k in ("basol", "basias_count", "icpe_count", "sis",
                                                                                  "pollution_constatee", "activite_passee", "type_friche")
                 else "non")
        tlbl = {"float": "nombre", "int": "entier", "bool": "oui / non", "enum": "liste", "list": "liste séparée par |", "str": "texte"}[typ]
        w.writerow([k, lbl, tlbl, ", ".join(vals or []), oblig, src, conf])
    w.writerow(["<colonne>__source", "Source de la valeur de <colonne>", "texte", "", "non", "", ""])
    w.writerow(["<colonne>__date", "Date de la valeur de <colonne>", "AAAA-MM-JJ ou AAAA", "", "non", "", ""])
    w.writerow(["<colonne>__confiance", "Confiance de la valeur de <colonne>", "liste", "eleve, moyen, faible", "non", "", ""])
    return buf.getvalue()


def catalog():
    return {k: {"label": v[0], "type": v[1], "source": v[2], "confiance": v[3], "valeurs": v[4]} for k, v in CATALOG.items()}


# ------------------------------------------------------------- paramètres
def apply_params() -> list[str]:
    """Réapplique les paramètres de l'organisme courant (pendant que ses calculs sont autorisés)."""
    from . import tenant
    tenant.PARAMS.invalidate(tenant.org())
    return []


def params_view() -> dict:
    ov = db.param_overrides()
    return {"parametres": params.snapshot(ov), "n_modifies": len(ov)}


def params_save(changes: list[dict]) -> dict:
    """changes : [{path, value, note}] ; value None = retour à la valeur par défaut."""
    errors = []
    for ch in changes:
        path = ch.get("path")
        if ch.get("value") is None:
            db.delete_param(path)
            continue
        val, err = params.validate(path, ch["value"])
        if err:
            label = params.SCHEMA_BY_PATH.get(path, {}).get("label", path)
            errors.append(f"{label} : {err}")
            continue
        if val == params.default(path) and not ch.get("note"):
            db.delete_param(path)
        else:
            db.set_param(path, val, ch.get("note"))
    apply_params()
    return {"erreurs": errors, **params_view()}


def params_reset(path: str | None = None):
    db.delete_param(path)
    apply_params()


def params_export() -> dict:
    return {"format": "frichescore-parametres", "version": 1, "exporte_le": db.now(),
            "parametres": {p: o for p, o in db.param_overrides().items()}}


def params_import(data: dict) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("parametres"), dict):
        raise ValueError("fichier de paramètres invalide (clé « parametres » attendue)")
    changes = []
    for p, o in data["parametres"].items():
        if isinstance(o, dict) and "value" in o:
            changes.append({"path": p, "value": o["value"], "note": o.get("note")})
        else:
            changes.append({"path": p, "value": o})
    db.delete_param()
    return params_save(changes)


# -------------------------------------------------------------- calendrier
def calendar_view() -> dict:
    data = calendar.load()
    return {"calendrier": data, "fichier": str(calendar.path()),
            "dispositifs": sorted(calendar.DISPOSITIFS), "conditions": sorted(calendar.CONDITIONS)}


def calendar_save(data: dict) -> list[str]:
    return calendar.save(data)


# -------------------------------------------------------------- connecteurs
def enrich_communes(codes: list[str] | None = None, progress=None, refresh: bool = False) -> dict:
    """Indicateurs communaux réels (API Géo, zonage ABC, LOVAC, SITADEL, ENAF) pour les communes
    données, ou pour toutes les communes portant des friches réelles."""
    from .ingest import territoire
    codes = codes or db.real_friche_communes()
    if not codes:
        return {"communes": 0, "indicateurs": {}, "fichiers": {},
                "erreurs": ["Aucune friche réelle dans la base : importez d'abord un périmètre ou un fichier CSV."]}
    existing = {c: db.get_commune(c) for c in codes}
    recs, rep = territoire.enrich_communes(codes, {k: v for k, v in existing.items() if v}, refresh, progress)
    for c, rec in recs.items():
        if not rec.values:
            continue
        rec.values["code_insee"] = c
        base = existing.get(c)
        db.upsert_commune(c, base.merged_with(rec) if base else rec)
        if rec.has("epci"):
            db.update_friche_epci(c, rec.get("epci"))
    return rep


def enrich_sites(ids: list[str] | None = None, adresse: bool = True, progress=None) -> dict:
    """Indicateurs de site (gare, transports collectifs, ensoleillement, risques) pour les friches données,
    ou pour toutes les friches réelles."""
    from .ingest import site as S_
    ids = ids or db.real_friche_ids()
    if not ids:
        return {"sites": 0, "indicateurs": {}, "sources": {},
                "erreurs": ["Aucune friche réelle dans la base : importez d'abord un périmètre ou un fichier CSV."]}
    sites = db.load_sites({"type": "ids", "values": ids})
    vals, rep = S_.enrich_sites(list(sites.values()), adresse, progress)
    for sid, v in vals.items():
        if v:
            db.update_friche_values(sid, v)
    return rep


def ingest_cartofriches(codes: list[str], georisques: bool = True, dvf: bool = True, progress=None,
                        communes: bool = True, sites: bool = True, max_codes: int | None = 50,
                        refresh: bool = False) -> dict:
    """Import d'un périmètre. `progress(etape, fait, total)` est appelé au fil de l'eau.
    Les bases Géorisques sont interrogées en parallèle (6 friches à la fois)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def tick(etape, fait=0, total=0):
        if progress:
            progress(etape, fait, total)

    report = {"importees": 0, "communes_dvf": 0, "erreurs": [], "avertissements": [], "communes_interrogees": []}
    codes = codes[:max_codes] if max_codes else list(codes)
    all_recs = []
    done = 0
    tick("Cartofriches", 0, len(codes))
    with ThreadPoolExecutor(max_workers=4 if len(codes) > 4 else 1) as ex:
        futs = {ex.submit(connectors.fetch_cartofriches, code): code for code in codes}
        for f in as_completed(futs):
            code = futs[f]
            try:
                recs, warns = f.result()
                report["avertissements"] += [f"{code} : {w}" for w in warns]
                report["communes_interrogees"].append(code)
                all_recs += recs
            except connectors.ConnectorError as e:
                report["erreurs"].append(f"{code} : Cartofriches — {e.message}")
            except Exception as e:  # noqa: BLE001
                report["erreurs"].append(f"{code} : Cartofriches — {type(e).__name__}")
            done += 1
            tick(f"Cartofriches : commune {code}", done, len(codes))
    if georisques and all_recs:
        done = 0
        tick("Géorisques : bases de pollution", 0, len(all_recs))
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(connectors.enrich_georisques, r): r for r in all_recs}
            for f in as_completed(futs):
                rec = futs[f]
                try:
                    _, errs = f.result()
                    report["erreurs"] += [f"{rec.get('id')} : {e}" for e in errs]
                except Exception as e:  # noqa: BLE001 — une friche en échec ne bloque pas les autres
                    report["erreurs"].append(f"{rec.get('id')} : Géorisques — {type(e).__name__}")
                done += 1
                if done % 5 == 0 or done == len(all_recs):
                    tick("Géorisques : bases de pollution", done, len(all_recs))
    tick("Enregistrement", 0, 1)
    for rec in all_recs:
        db.upsert_friche(rec, "cartofriches")
    report["importees"] = len(all_recs)
    report["ids_importes"] = [r.get("id") for r in all_recs]
    if dvf:
        y = date.today().year
        found = sorted({r.get("code_insee") for r in all_recs if r.get("code_insee")})
        for i, code in enumerate(found):
            tick(f"DVF : prix de marché ({code})", i, len(found))
            ind, errs = connectors.fetch_dvf(code, [y - 4, y - 3, y - 2, y - 1])
            errs = [e for e in errs if "404" not in e or str(y - 1) not in e]  # dernière année pas toujours publiée
            report["erreurs"] += [f"{code} : {e}" for e in errs]
            if ind:
                existing = db.get_commune(code)
                rec = connectors.dvf_commune_record(code, ind)
                db.upsert_commune(code, existing.merged_with(rec) if existing else rec)
                report["communes_dvf"] += 1
    if communes and all_recs:
        found = sorted({r.get("code_insee") for r in all_recs if r.get("code_insee")})
        rep = enrich_communes(found, progress, refresh)
        report["indicateurs_communaux"] = rep["indicateurs"]
        report["erreurs"] += rep["erreurs"]
    if sites and all_recs:
        rep = enrich_sites([r.get("id") for r in all_recs], True, progress)
        report["indicateurs_sites"] = rep["indicateurs"]
        report["erreurs"] += rep["erreurs"]
    return report


# ------------------------------------------------------- extraction IA
def ia_status() -> dict:
    import os
    from .ia import extraction as E
    from . import tenant
    quota = int(os.environ.get("FRICHESCORE_IA_QUOTA_MOIS", "200"))
    o = db.org_get(tenant.org()) if tenant.org() else None
    if o and o.get("quota_ia") is not None:
        quota = int(o["quota_ia"])  # quota propre à l'organisme (abonnement)
    return {"active": E.configured(), "modele": E.MODEL, "quota_mois": quota, **db.ia_month_stats(),
            "prix": {"entree_usd_par_million": E.PRIX_ENTREE, "sortie_usd_par_million": E.PRIX_SORTIE}}


def ia_start(sid: str, fichier: str, data: bytes) -> tuple[str, callable]:
    """Vérifie et enregistre le document, puis renvoie (id d'extraction, fonction à exécuter en tâche de fond)."""
    ensure_visible(sid)
    import hashlib
    import uuid
    from .ia import extraction as E
    st = ia_status()
    if not st["active"]:
        raise E.IAError("extraction IA non activée : renseigner ANTHROPIC_API_KEY (voir README)")
    if st["documents"] >= st["quota_mois"]:
        raise E.IAError(f"quota mensuel atteint ({st['quota_mois']} documents) pour votre organisme")
    sites = db.load_sites({"type": "ids", "values": [sid]})
    if sid not in sites:
        raise KeyError(sid)
    E.check_pdf(data)
    sha = hashlib.sha256(data).hexdigest()
    folder = db.DB_PATH.parent / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sha}.pdf").write_bytes(data)  # conservé localement pour vérifier les citations
    eid = uuid.uuid4().hex[:12]
    db.ia_create(eid, sid, fichier, sha, len(data), E.MODEL)
    rec = sites[sid]
    contexte = (f"friche « {rec.get('nom')} », commune de {rec.get('commune')} ({rec.get('code_insee')}), "
                f"surface {rec.get('surface_m2') or 'inconnue'} m², type {rec.get('type_friche') or 'inconnu'}")

    def run(progress=None):
        if progress:
            progress("Analyse du document par l'IA (de quelques secondes à quelques minutes)", 0, 1)
        try:
            r = E.call_model(data, contexte)
        except E.IAError as e:
            db.ia_update(eid, statut="erreur", erreur=str(e))
            raise
        res = E.normalise(r["resultat"])
        db.ia_update(eid, statut="termine", resultat=res, usage=r["usage"], modele=r["modele"], cout_usd=E.cout_usd(r["usage"]))
        return {"extraction": eid, "valeurs": len(res["valeurs"]), "cout_usd": E.cout_usd(r["usage"])}
    return eid, run


def ia_validate(sid: str, eid: str, items: list[dict], auteur: str | None, weights, d5) -> dict:
    """Enregistre comme données terrain les valeurs retenues par l'utilisateur (avec page et citation)."""
    from .ia import extraction as E
    ex = db.ia_get(eid)
    if not ex or ex["friche_id"] != sid:
        raise KeyError(eid)
    if ex["statut"] != "termine":
        raise ValueError("extraction non terminée")
    res = ex["resultat"]
    vals = res["valeurs"]
    saved, errors = [], []
    for it in items:
        i = it.get("index")
        if not isinstance(i, int) or not (0 <= i < len(vals)) or not it.get("retenir"):
            continue
        v = vals[i]
        valeur = it.get("valeur") if it.get("valeur") not in (None, "") else v.get("valeur")
        src = (f"« {ex['fichier']} », p. {v.get('page')} — extrait par IA, validé"
               f"{' par ' + auteur if auteur else ''} : « {str(v.get('citation'))[:160]} »")
        try:
            field_add(sid, v["indicateur"], valeur, src, E.default_date(res), it.get("confiance") or v.get("confiance") or "moyen",
                      auteur, weights, d5)
            saved.append(v["indicateur"])
        except ValueError as e:
            errors.append(f"{v.get('libelle')} : {e}")
    if saved:
        db.ia_update(eid, valide_le=db.now(), valide_par=auteur)
    return {"enregistrees": saved, "erreurs": errors}


def ia_question(question: str, perimeter: dict, perimeter_label: str, usage, weights, d5) -> dict:
    """Question en langage naturel -> hypothèses (IA) -> scénario calculé par le moteur."""
    from .ia import client as IC
    from .ia import questions as Q
    question = (question or "").strip()
    if len(question) < 5:
        raise ValueError("question trop courte")
    if len(question) > 2000:
        raise ValueError("question trop longue (2 000 caractères maximum)")
    ctx = (f"périmètre : {perimeter_label} ; usage cible : {C.USAGES.get(usage, 'meilleur usage par site') if usage else 'meilleur usage par site'}")
    r = Q.interpret(question, ctx)
    cout = IC.cout_usd(r["usage"])
    db.ia_log("contre_analyse", question, r["usage"], cout)
    out = {"question": question, "interpretation": r, "cout_usd": cout, "scenario": None}
    if r["hypotheses"]:
        out["scenario"] = stress(perimeter, usage, weights, d5, r["hypotheses"], r["top_n"])
    return out


def ia_note(sid: str, usage, weights, d5) -> dict:
    """Note d'analyse rédigée par l'IA à partir du dossier calculé par FricheScore."""
    from .ia import client as IC
    from .ia import redaction as RD
    d = site_detail(sid, weights, d5, usage)
    if not d["evaluable"]:
        raise ValueError("site non évaluable : pas de note possible")
    u = d["usage"]
    r = RD.note_site(RD.dossier_site(d, u))
    cout = IC.cout_usd(r["usage"])
    db.ia_log("note_site", sid, r["usage"], cout)
    db.ia_note_save(sid, u, r["note"], r["modele"], cout)
    return db.ia_note_latest(sid, u)


def ia_jumeau(a: dict, b: dict, weights, d5) -> dict:
    from .ia import client as IC
    from .ia import redaction as RD
    res = compare_sites(a, b, weights, d5)
    r = RD.conclusion_jumeau(RD.dossier_jumeau(res))
    cout = IC.cout_usd(r["usage"])
    db.ia_log("conclusion_jumeau", f"{res['a'].get('nom')} / {res['b'].get('nom')}", r["usage"], cout)
    return {**r["conclusion"], "cout_usd": cout, "modele": r["modele"]}


# ------------------------------------------------------- signaux d'opportunité
def signaux_view(non_vus: bool = False) -> dict:
    rows = db.signals_list(non_vus=non_vus)
    sites = db.load_sites({"type": "ids", "values": sorted({r["friche_id"] for r in rows})}) if rows else {}
    for r in rows:
        s_ = sites.get(r["friche_id"])
        r["nom"] = s_.get("nom") if s_ else None
        r["commune"] = s_.get("commune") if s_ else None
        r["supprimee"] = s_ is None
    nv = [r for r in db.signals_list(non_vus=True)]
    return {"signaux": rows, "non_vus": len(nv), "opportunites_non_vues": sum(r["niveau"] == "opportunite" for r in nv)}


def signaux_rechercher(progress=None) -> dict:
    from . import signaux as SG
    rep = SG.scan_bodacc(db.load_sites({"type": "tout"}), progress)
    if not rep["sirens"]:
        rep["erreurs"].append("Aucune friche réelle n'a de SIREN connu (exploitant ICPE via Géorisques, ou colonne "
                              "« siren_proprietaire » de votre CSV / saisie terrain).")
    return rep
