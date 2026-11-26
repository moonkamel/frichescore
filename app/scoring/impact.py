"""Dimension 5 — Impact et alignement politique (optionnelle).

Mesure la contribution du site aux objectifs ZAN (loi Climat et résilience,
objectif de réduction de moitié de la consommation d'espaces 2021-2031) et son
éligibilité POTENTIELLE aux dispositifs de financement. L'éligibilité est une
présomption fondée sur des critères simples : elle doit être confirmée auprès
du financeur.
"""
from __future__ import annotations

from .record import Tracker


def compute_impact(t: Tracker, usage: str, market: dict, hyp: dict | None = None) -> dict:
    hyp = hyp or {}
    no_subsidy = bool(hyp.get("sans_subventions"))
    s = 25.0
    elig: list[dict] = []
    surf_ha = float(t.read("surface_m2", 0, 1)) / 10_000

    if t.read("artificialise", True, 1):
        s += t.add("Recyclage d'un sol déjà artificialisé (évite la consommation d'ENAF)", 12, "artificialise")
    else:
        s += t.add(f"Consomme {surf_ha:.1f} ha d'espaces naturels, agricoles ou forestiers (budget ZAN)", -min(25, 5 + 4 * surf_ha), "artificialise")
    conso = t.read("conso_enaf_ha_2011_2021", None, 1)
    if conso:
        annual = conso / 10
        years = surf_ha / annual if annual else 0
        if years >= 1:
            s += t.add(f"Équivaut à {years:.1f} an(s) de consommation d'espaces de la commune", min(18, 6 * years), "conso_enaf_ha_2011_2021")
        elif years >= 0.3:
            s += t.add(f"Équivaut à {years*12:.0f} mois de consommation d'espaces communale", 5, "conso_enaf_ha_2011_2021")
    prog = t.read("programme_territorial", None, 1)
    if prog in ("ACV", "PVD"):
        s += t.add(f"Commune {'Action Cœur de Ville' if prog == 'ACV' else 'Petites Villes de Demain'}", 10, "programme_territorial")
    if t.read("qpv_proximite", False, 0.5) and usage in ("logement", "equipement", "renaturation"):
        s += t.add("À proximité d'un quartier prioritaire (QPV)", 6, "qpv_proximite")
    if usage == "activite" and t.read("territoire_industrie", False, 0.5):
        s += t.add("Territoire d'industrie : réindustrialisation prioritaire", 8, "territoire_industrie")

    is_friche = not t.rec.get("site_extension")
    if not no_subsidy and is_friche:
        deficit = market["bilan_net_haut"] < 0 or (market["bilan_net_bas"] < 0 and not market["marchand"])
        if usage in ("logement", "activite", "tertiaire", "equipement") and (market["bilan_net_bas"] < 0):
            elig.append({"dispositif": "fonds_vert_recyclage", "nom": "Fonds Vert — recyclage foncier des friches",
                         "motif": "friche + bilan d'opération potentiellement déficitaire"})
            s += t.add("Éligibilité probable au Fonds Vert « recyclage foncier »", 15 if deficit else 8, None)
        if usage == "activite" and surf_ha >= 10:
            elig.append({"dispositif": "sites_cles_en_main", "nom": "France 2030 — sites clés en main",
                         "motif": f"emprise de {surf_ha:.0f} ha à vocation industrielle"})
            s += t.add("Candidat potentiel « sites clés en main » France 2030", 12, "surface_m2")
        if usage == "renaturation":
            elig.append({"dispositif": "fonds_vert_renaturation", "nom": "Fonds Vert — renaturation des villes",
                         "motif": "projet de désimperméabilisation / renaturation"})
            s += t.add("Éligible aux aides à la renaturation", 12, None)
        pollue = t.rec.get("basol") or t.rec.get("sis") or (t.rec.get("basias_count") or 0) > 0
        if pollue and usage != "photovoltaique":
            elig.append({"dispositif": "ademe_friches_polluees", "nom": "ADEME — reconversion des friches polluées",
                         "motif": "site pollué ou potentiellement pollué"})
            s += t.add("Éligible potentiel aux aides ADEME friches polluées", 6, "basol")
        if usage == "photovoltaique":
            elig.append({"dispositif": "cre_terrains_degrades", "nom": "Appels d'offres CRE — PV sur terrains dégradés",
                         "motif": "friche artificialisée"})
            s += t.add("Accès aux appels d'offres CRE pour terrains dégradés", 8, None)
        region_fund = t.rec.get("fonds_regional")
        if region_fund:
            elig.append({"dispositif": "fonds_regional", "nom": region_fund, "motif": "dispositif régional friches"})
            s += t.add(f"Dispositif régional : {region_fund}", 6, "fonds_regional")

    s = max(0.0, min(100.0, s))
    phrase = (f"Impact {'fort' if s >= 65 else 'moyen' if s >= 45 else 'limité'} : "
              + (f"{len(elig)} dispositif(s) de financement potentiellement mobilisable(s)." if elig else "pas de dispositif de financement identifié."))
    return {"score": round(s, 1), "phrase": phrase, "eligibilites": elig}
