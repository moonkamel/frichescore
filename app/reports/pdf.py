"""Exports PDF : fiche de synthèse par site et rapport de pré-cadrage.

Conçus pour être présentés tels quels en comité d'investissement ou à une
collectivité : chaque score est accompagné de ses facteurs déterminants, de ses
sources et de son niveau de confiance.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from ..scoring.config import DIMENSIONS, USAGES
from ..scoring.indicators import CATALOG, label
from ..scoring.record import age_years

FONT, FONT_B = "Helvetica", "Helvetica-Bold"
_FONT_USED = None
# Police Unicode : DejaVu (Linux / Docker) ou Arial (Windows), sinon Helvetica avec substitutions
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/DejaVuSans.ttf", "C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]
for reg, bold in _FONT_CANDIDATES:
    if Path(reg).exists() and Path(bold).exists():
        try:
            pdfmetrics.registerFont(TTFont("UniSans", reg))
            pdfmetrics.registerFont(TTFont("UniSans-Bold", bold))
            FONT, FONT_B = "UniSans", "UniSans-Bold"
            _FONT_USED = reg
            break
        except Exception:  # noqa: BLE001 — police illisible : on essaie la suivante
            continue

INK = colors.HexColor("#1c2733")
MUTED = colors.HexColor("#5b6773")
LINE = colors.HexColor("#d5dbe1")
ACCENT = colors.HexColor("#1f6f5c")
WARN = colors.HexColor("#b5541c")
BG = colors.HexColor("#f3f5f7")

ST = {
    "h1": ParagraphStyle("h1", fontName=FONT_B, fontSize=16, leading=20, textColor=INK, spaceAfter=4),
    "h2": ParagraphStyle("h2", fontName=FONT_B, fontSize=11.5, leading=15, textColor=ACCENT, spaceBefore=9, spaceAfter=4),
    "p": ParagraphStyle("p", fontName=FONT, fontSize=8.8, leading=12, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("s", fontName=FONT, fontSize=7.4, leading=9.5, textColor=MUTED),
    "cell": ParagraphStyle("c", fontName=FONT, fontSize=7.8, leading=10, textColor=INK),
    "cellb": ParagraphStyle("cb", fontName=FONT_B, fontSize=7.8, leading=10, textColor=INK),
    "warn": ParagraphStyle("w", fontName=FONT_B, fontSize=8.5, leading=11.5, textColor=WARN),
}


def _esc(s) -> str:
    s = "" if s is None else str(s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if FONT == "Helvetica" or "dejavu" not in str(_FONT_USED).lower():
        for a, b in (("≈", "~"), ("≥", ">="), ("≤", "<="), ("→", "->"), ("−", "-"), ("✓", "v"), ("⚠", "!"),
                     ("⏱", ""), ("▲", "+"), ("▼", "-")):
            s = s.replace(a, b)
    return s


def P(s, st="p"):
    return Paragraph(_esc(s), ST[st])


def eur(v) -> str:
    v = float(v)
    if abs(v) >= 1e6:
        return f"{v/1e6:,.1f} M€".replace(",", " ").replace(".", ",")
    if abs(v) >= 1e3:
        return f"{v/1e3:,.0f} k€".replace(",", " ")
    return f"{v:,.0f} €".replace(",", " ")


def _table(data, widths, header=True, zebra=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("FONT", (0, 0), (-1, -1), FONT, 7.8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("FONT", (0, 0), (-1, 0), FONT_B, 7.8), ("BACKGROUND", (0, 0), (-1, 0), BG),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    t.setStyle(TableStyle(style))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 7)
    canvas.setFillColor(MUTED)
    w = doc.pagesize[0]
    canvas.drawString(15 * mm, 9 * mm, "FricheScore — estimation de risque, pas un devis. Les fourchettes et niveaux de confiance font partie du résultat.")
    canvas.drawRightString(w - 15 * mm, 9 * mm, f"p. {doc.page}")
    canvas.restoreState()


def _bar(v, w=34 * mm, h=3.2 * mm, invert=False):
    """Petite barre horizontale dessinée dans un tableau."""
    v = max(0, min(100, float(v)))
    good = (100 - v) if invert else v
    col = ACCENT if good >= 60 else colors.HexColor("#c49a1c") if good >= 40 else WARN
    t = Table([["", ""]], colWidths=[w * v / 100 + 0.01, w * (1 - v / 100) + 0.01], rowHeights=[h])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), col), ("BACKGROUND", (1, 0), (1, 0), BG),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _demo_banner(rec_values: dict) -> list:
    if str(rec_values.get("_origine", "")).startswith("demo"):
        return [P("⚠ Données de démonstration synthétiques : ce document illustre la méthode et ne décrit pas un site réel.", "warn"),
                Spacer(0, 2 * mm)]
    return []


# ------------------------------------------------------------- fiche site
def site_pdf(detail: dict, usage: str, g: dict, weights: dict, profile_label: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=15 * mm, title=f"FricheScore — {detail['values'].get('nom')}")
    v = detail["values"]
    ur = detail["usages"][usage]
    el = []
    el += _demo_banner(v)
    el.append(P(f"Fiche de synthèse — {v.get('nom')}", "h1"))
    el.append(P(f"{v.get('commune')} ({v.get('code_insee')}) · {v.get('epci') or ''} · Surface {float(v.get('surface_m2', 0))/10000:.2f} ha · "
                f"Type : {v.get('type_friche')} · Cartofriches : {v.get('statut_cartofriches', 'n.r.')} · Réf. {v.get('id')}", "small"))
    el.append(P(f"Édité le {date.today().strftime('%d/%m/%Y')} · Usage évalué : {USAGES[usage]} · Profil : {profile_label} · "
                f"Pondérations : " + ", ".join(f"{DIMENSIONS[k]} {weights.get(k, 0):.0f}" for k in DIMENSIONS), "small"))
    el.append(Spacer(0, 3 * mm))

    conf = g["confiance"]
    conf_lbl = "élevée" if conf >= 0.8 else "moyenne" if conf >= 0.6 else "faible"
    head = [[P(f"FricheScore {g['global']:.0f} / 100", "h1"),
             P(f"Score brut {g['brut']:.0f} − pénalité de confiance {g['penalite']:.0f} pt(s). Confiance des données : "
               f"{conf_lbl} ({conf:.2f}).{' USAGE RÉDHIBITOIRE : ' + '; '.join(ur['blockers']) if ur['blockers'] else ''}", "p")]]
    t = Table(head, colWidths=[72 * mm, 108 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BG), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("BOX", (0, 0), (-1, -1), 0.5, LINE)]))
    el.append(t)

    el.append(P("Les trois facteurs qui pèsent le plus", "h2"))
    for d in g["drivers"]:
        el.append(P(f"{'▲' if d['effet'] > 0 else '▼'} {d['label']} ({d['effet']:+.1f} pt sur le global)"))

    el.append(P("Les cinq dimensions", "h2"))
    rows = [["Dimension", "Note", "", "Lecture"]]
    for k in ("d1", "d2", "d3", "d4", "d5"):
        val = ur["dims"][k]
        rows.append([P(DIMENSIONS[k] + (" (plus c'est haut, plus c'est pénalisant)" if k == "d3" else ""), "cellb"),
                     f"{val:.0f}", _bar(val, invert=(k == "d3")), P(ur["phrases"][k], "cell")])
    el.append(_table(rows, [42 * mm, 11 * mm, 36 * mm, 91 * mm]))

    el.append(P("Pertinence par usage", "h2"))
    rows = [["Usage", "Potentiel (D1)", "Coût (D2)", "Friction (D3)", "Marché (D4)", "Global", "Statut"]]
    for u, x in detail["usages"].items():
        gg = detail["globals"][u]
        rows.append([USAGES[u], f"{x['dims']['d1']:.0f}", f"{x['dims']['d2']:.0f}", f"{x['dims']['d3']:.0f}",
                     f"{x['dims']['d4']:.0f}", f"{gg['global']:.0f}", "rédhibitoire" if x["blockers"] else ""])
    el.append(_table(rows, [42 * mm, 23 * mm, 20 * mm, 22 * mm, 22 * mm, 18 * mm, 33 * mm]))

    c = ur["cost"]
    el.append(KeepTogether([
        P("Coût de remise en état (fourchette)", "h2"),
        P(f"{c['phrase']} Fourchette : {eur(c['total_bas'])} à {eur(c['total_haut'])} "
          f"({c['eur_m2_bas']:.0f} à {c['eur_m2_haut']:.0f} €/m² de terrain)."),
        _table([["Poste", "Bas", "Haut"]] + [[P(p["label"], "cell"), eur(p["bas"]), eur(p["haut"])] for p in c["postes"]],
               [120 * mm, 30 * mm, 30 * mm]),
    ]))
    fr = ur["friction"]
    el.append(KeepTogether([
        P("Friction réglementaire et délais", "h2"),
        P(f"{fr['phrase']} Délai indicatif jusqu'au démarrage : ~{fr['delai_total_mois']} mois "
          f"(dont {fr['delai_additionnel_mois']} mois liés aux contraintes)."),
        _table([["Contrainte", "Points", "Mois"]] + [[P(f["label"], "cell"), f"{f['points']:.0f}", f"{f['mois']:.0f}"] for f in fr["facteurs"]],
               [130 * mm, 25 * mm, 25 * mm]) if fr["facteurs"] else P("Aucune contrainte identifiée."),
    ]))
    m = ur["market"]
    el.append(P("Équilibre économique (compte à rebours simplifié)", "h2"))
    if m["marchand"]:
        el.append(P(f"Charge foncière admissible {eur(m['valeur_residuelle_bas'])} à {eur(m['valeur_residuelle_haut'])} "
                    f"({m['hypothese']}) ; acquisition estimée {eur(m['acquisition_bas'])} à {eur(m['acquisition_haut'])} ; "
                    f"bilan net {eur(m['bilan_net_bas'])} à {eur(m['bilan_net_haut'])}. Ordre de grandeur à confirmer par un pré-bilan."))
    else:
        el.append(P("Usage non marchand : le bilan se lit en coût net à financer "
                    f"({eur(-m['bilan_net_haut'])} à {eur(-m['bilan_net_bas'])})."))
    im = ur["impact"]
    el.append(P("Financements potentiellement mobilisables", "h2"))
    if im["eligibilites"]:
        for e in im["eligibilites"]:
            el.append(P(f"• {e['nom']} — {e['motif']} (présomption à confirmer auprès du financeur)"))
    else:
        el.append(P("Aucun dispositif identifié."))
    for z in detail.get("zan", []):
        el.append(P(f"⏱ Fenêtre {z['statut']} : {z['fenetre_nom']} — clôture {z['cloture']}, dossier prêt vers {z['maturite']['date']}."))

    alerts = [a["message"] for a in ur["alerts"]] + ur["blockers"]
    if alerts:
        el.append(P("Alertes", "h2"))
        for a in alerts:
            el.append(P(f"• {a}", "warn"))

    el.append(P("Études complémentaires recommandées avant engagement", "h2"))
    for r in detail["recommandations"] or [{"priorite": "-", "etude": "Aucune", "motif": ""}]:
        el.append(P(f"[{r['priorite']}] {r['etude']} — {r['motif']}"))

    el.append(PageBreak())
    el.append(P("Fiche de vie de la donnée", "h2"))
    el.append(P("Chaque indicateur utilisé avec sa source, sa date et son niveau de confiance. Les indicateurs anciens "
                "ou faibles réduisent le score via la pénalité de confiance.", "small"))
    rows = [["Indicateur", "Valeur", "Source", "Date", "Confiance"]]
    for item in detail["fiche_vie"]:
        rows.append([P(item["label"], "cell"), P(item["valeur"], "cell"), P(item["source"], "cell"),
                     item.get("date") or "n.r.", f"{item['confiance']}{' (ancienne)' if item.get('ancienne') else ''}"])
    el.append(_table(rows, [48 * mm, 30 * mm, 58 * mm, 18 * mm, 26 * mm]))
    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def fiche_vie(values: dict, prov: dict) -> list[dict]:
    out = []
    for k, v in values.items():
        if k.startswith("_") or k in ("id",):
            continue
        p = prov.get(k) or {}
        a = age_years(p.get("date"))
        val = v
        if isinstance(v, bool):
            val = "oui" if v else "non"
        elif isinstance(v, list):
            val = ", ".join(map(str, v))
        elif isinstance(v, float):
            val = f"{v:,.1f}".replace(",", " ")
        out.append({"key": k, "label": label(k), "valeur": str(val), "source": p.get("source") or "non tracée",
                    "date": p.get("date"), "confiance": p.get("confiance") or "n.r.", "methode": p.get("methode"),
                    "age_ans": round(a, 1) if a is not None else None, "ancienne": a is not None and a > 10,
                    "ordre": list(CATALOG).index(k) if k in CATALOG else 999})
    out.sort(key=lambda x: x["ordre"])
    return out


# ------------------------------------------------------ rapport de pré-cadrage
def report_pdf(ctx: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=14 * mm, title="FricheScore — Rapport de pré-cadrage")
    el = []
    if ctx.get("demo"):
        el.append(P("⚠ Données de démonstration synthétiques : ce rapport illustre la méthode et ne décrit pas des sites réels.", "warn"))
    el.append(P("Rapport de pré-cadrage — priorisation des friches", "h1"))
    el.append(P(f"Périmètre : {ctx['perimetre_label']} · Usage : {ctx['usage_label']} · Profil : {ctx['profil']} · "
                f"Édité le {date.today().strftime('%d/%m/%Y')}", "small"))
    el.append(Spacer(0, 2 * mm))
    s = ctx["stats"]
    el.append(P(f"{s['n']} friches dans le périmètre : {s['evaluables']} évaluables, {s['non_evaluables']} non évaluables "
                f"(données minimales absentes — listées en annexe). Short-list proposée : {len(ctx['top'])} sites."))
    el.append(P("Pondérations : " + ", ".join(f"{DIMENSIONS[k]} {ctx['weights'].get(k, 0):.0f}" for k in DIMENSIONS)
                + ". La friction réglementaire entre dans le score global sous la forme (100 − friction).", "small"))

    el.append(P("Short-list", "h2"))
    rows = [["#", "Site", "Commune", "Usage", "Score", "Potentiel", "Coût", "Friction", "Marché", "Impact", "Conf.",
             "Coût €/m²", "Délai", "Robustesse"]]
    for r in ctx["top"]:
        d = r["dims"]
        rows.append([r["rang"], P(r["nom"], "cell"), P(r["commune"], "cell"), P(USAGES[r["usage"]], "cell"),
                     f"{r['global']:.0f}", f"{d['d1']:.0f}", f"{d['d2']:.0f}", f"{d['d3']:.0f}", f"{d['d4']:.0f}",
                     f"{d['d5']:.0f}", f"{r['confiance']:.2f}", f"{r['cout_bas']:.0f}-{r['cout_haut']:.0f}",
                     f"{r['delai']} m", P(r.get("robustesse", "-"), "cell")])
    el.append(_table(rows, [8 * mm, 52 * mm, 30 * mm, 28 * mm, 13 * mm, 16 * mm, 13 * mm, 15 * mm, 15 * mm, 14 * mm,
                            13 * mm, 20 * mm, 13 * mm, 20 * mm]))
    if ctx.get("stress"):
        el.append(P("Contre-analyse", "h2"))
        el.append(P(ctx["stress"]["synthese"]))
        if ctx.get("mc"):
            el.append(P(ctx["mc"]["synthese"]))

    el.append(PageBreak())
    el.append(P("Justification site par site", "h2"))
    for r in ctx["top"]:
        block = [P(f"{r['rang']}. {r['nom']} — {r['commune']} · {USAGES[r['usage']]} · FricheScore {r['global']:.0f} "
                   f"(confiance {r['confiance']:.2f})", "cellb")]
        block.append(P("Facteurs déterminants : " + " ; ".join(f"{d['label']} ({d['effet']:+.1f})" for d in r["drivers"]), "cell"))
        block.append(P(r["phrases"]["d2"] + " " + r["phrases"]["d3"], "cell"))
        if r["recos"]:
            block.append(P("Étude prioritaire : " + r["recos"][0]["etude"], "cell"))
        if r["blockers"]:
            block.append(P("Rédhibitoire : " + "; ".join(r["blockers"]), "warn"))
        block.append(Spacer(0, 2.5 * mm))
        el.append(KeepTogether(block))

    if ctx["non_evaluables"]:
        el.append(P("Annexe — friches non évaluables", "h2"))
        el.append(P("L'outil refuse de scorer ces sites plutôt que de produire un score faux.", "small"))
        rows = [["Réf.", "Site", "Commune", "Données manquantes"]]
        for n in ctx["non_evaluables"]:
            rows.append([n["id"], P(n["nom"] or "", "cell"), n["commune"] or "", P("; ".join(n["manquants"]), "cell")])
        el.append(_table(rows, [38 * mm, 70 * mm, 40 * mm, 120 * mm]))

    el.append(P("Annexe — sources mobilisées", "h2"))
    rows = [["Source", "Indicateurs", "Dates", "Confiance"]]
    for s_ in ctx["sources"]:
        rows.append([P(s_["source"], "cell"), str(s_["n"]), s_["dates"], s_["confiance"]])
    el.append(_table(rows, [110 * mm, 25 * mm, 60 * mm, 40 * mm]))
    pm = ctx.get("parametres_modifies") or []
    el.append(P("Annexe — paramètres de calcul", "h2"))
    if pm:
        el.append(P(f"{len(pm)} paramètre(s) calibré(s) par l'utilisateur par rapport aux valeurs par défaut :", "small"))
        rows = [["Groupe", "Paramètre", "Valeur retenue", "Défaut", "Justification"]]
        for x in pm:
            f_ = lambda v: " / ".join(f"{a:g}" for a in v) if isinstance(v, (list, tuple)) else f"{v:g}"
            rows.append([P(x["groupe"], "cell"), P(x["label"], "cell"), f_(x["valeur"]), f_(x["defaut"]), P(x["note"] or "—", "cell")])
        el.append(_table(rows, [55 * mm, 70 * mm, 35 * mm, 35 * mm, 70 * mm]))
    else:
        el.append(P("Paramètres par défaut de FricheScore (ordres de grandeur non calibrés localement).", "small"))
    el.append(Spacer(0, 3 * mm))
    el.append(P("Méthode : chaque friche reçoit une note 0-100 sur cinq dimensions (potentiel par usage, risque de surcoût de "
                "remise en état, friction réglementaire, attractivité économique, impact et financements). Le score global est "
                "une moyenne pondérée, diminuée d'une pénalité de confiance de 0 à 20 points selon la fraîcheur et la fiabilité "
                "des données. Les montants sont des fourchettes d'ordre de grandeur et ne remplacent ni diagnostic de pollution, "
                "ni étude de sol, ni pré-bilan d'aménagement.", "small"))
    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
