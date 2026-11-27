"""Signaux d'opportunité : ce qui rend un site « actionnable » ou change la donne.

Deux familles :
- **changements détectés lors des mises à jour** (comparaison avant / après) : cessation d'activité ICPE, projet
  apparu sur le site, zonage du PLU modifié, risque ou zone humide nouvellement recensés, statut Cartofriches ;
- **annonces légales BODACC** (DILA, données ouvertes) visant l'entreprise propriétaire ou exploitante du site,
  identifiée par son SIREN : procédure collective (sauvegarde, redressement, liquidation), vente ou cession,
  radiation. Une liquidation du propriétaire annonce souvent une mise en vente.

Seuls des identifiants d'entreprise (SIREN) sont utilisés. Aucun nom n'est repris des annonces : la dénomination
d'une entreprise individuelle est le nom d'une personne.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from . import db
from .ingest import connectors as K
from .scoring.record import Record

BODACC_URL = os.environ.get(
    "BODACC_URL", "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records")
BODACC_PAGE = "https://www.bodacc.fr/pages/annonces-commerciales-detail/?q.id=id:{id}"
FENETRE_MOIS = 36


# --------------------------------------------------------------- BODACC
def _famille(rec: dict) -> str | None:
    t = f"{rec.get('familleavis') or ''} {rec.get('familleavis_lib') or ''}".lower()
    if "collective" in t:
        return "procedure_collective"
    if "vente" in t or "cession" in t:
        return "vente"
    if "radiation" in t:
        return "radiation"
    return None


def _nature(rec: dict) -> str:
    j = rec.get("jugement")
    if isinstance(j, str):
        try:
            j = json.loads(j)
        except json.JSONDecodeError:
            return j[:160]
    if isinstance(j, dict):
        return str(j.get("nature") or j.get("famille") or "")[:160]
    return ""


def fetch_bodacc(siren: str, depuis: date | None = None) -> list[dict]:
    """Annonces BODACC d'une entreprise (procédures collectives, ventes et cessions, radiations), les plus récentes d'abord."""
    depuis = depuis or (date.today() - timedelta(days=30 * FENETRE_MOIS))
    rows = _bodacc_rows(siren, depuis)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        reg = json.dumps(r.get("registre") or "")
        if siren not in reg.replace(" ", ""):  # « like » peut être large : on revérifie le SIREN
            continue
        fam = _famille(r)
        if not fam:
            continue
        out.append({"id": r.get("id"), "date": str(r.get("dateparution") or "")[:10], "famille": fam,
                    "nature": _nature(r) or str(r.get("familleavis_lib") or ""), "tribunal": r.get("tribunal"),
                    "departement": r.get("numerodepartement")})
    return out


def _bodacc_query(siren: str, depuis: date) -> dict:
    """Le champ « registre » contient le SIREN avec ou sans espaces selon les annonces : recherche plein texte
    sur les deux écritures, puis vérification stricte du SIREN sur chaque annonce."""
    spaced = f"{siren[:3]} {siren[3:6]} {siren[6:]}"
    return {"where": f'("{siren}" or "{spaced}") and dateparution >= date\'{depuis.isoformat()}\'',
            "order_by": "dateparution desc", "limit": 100}


def _bodacc_rows(siren: str, depuis: date) -> list[dict]:
    with K._client(60) as c:
        data, _full, _st = K._get(c, BODACC_URL, _bodacc_query(siren, depuis))
    return data.get("results", []) if isinstance(data, dict) else []


TITRES = {
    "procedure_collective": ("opportunite", "Procédure collective visant {qui}"),
    "vente": ("opportunite", "Vente ou cession concernant {qui}"),
    "radiation": ("info", "Radiation de {qui}"),
}


def scan_bodacc(sites: dict[str, Record], progress=None, saisies_seulement: bool = False) -> dict:
    """Interroge le BODACC pour chaque SIREN lié à une friche réelle et enregistre les nouveaux signaux.
    `saisies_seulement` : uniquement les SIREN propriétaires saisis par l'organisme (signaux privés)."""
    cibles: dict[str, list[tuple[str, str]]] = {}  # siren -> [(friche, rôle)]
    for sid, rec in sites.items():
        if str(rec.get("_origine") or "").startswith("demo"):
            continue
        prov = rec.prov.get("siren_proprietaire")
        saisi = bool(prov and str(prov.methode or "").startswith("saisie terrain"))
        prop = K.siren_of(rec.get("siren_proprietaire")) if (saisi or not saisies_seulement) else None
        if prop:
            cibles.setdefault(prop, []).append((sid, "la société propriétaire"))
        if saisies_seulement:
            continue
        for s in rec.get("siren_exploitants") or []:
            s = K.siren_of(s)
            if s and s != prop:
                cibles.setdefault(s, []).append((sid, "l'exploitant ICPE du site"))
    rep = {"sirens": len(cibles), "annonces": 0, "nouveaux": 0, "erreurs": []}
    for i, (siren, liens) in enumerate(sorted(cibles.items())):
        if progress:
            progress("BODACC : annonces légales", i, len(cibles))
        try:
            annonces = fetch_bodacc(siren)
        except K.ConnectorError as e:
            rep["erreurs"].append(f"BODACC {siren} : {e.message}")
            if len(rep["erreurs"]) >= 5 and rep["annonces"] == 0:
                rep["erreurs"].append("BODACC injoignable : recherche interrompue")
                break
            continue
        rep["annonces"] += len(annonces)
        for a in annonces:
            niveau, titre = TITRES[a["famille"]]
            for sid, role in liens:
                if db.signal_add({"cle": f"bodacc:{sid}:{siren}:{a['id'] or a['date'] + a['famille']}", "friche_id": sid,
                                  "type": f"bodacc_{a['famille']}", "niveau": niveau, "titre": titre.format(qui=role),
                                  "detail": f"{a['nature']} (SIREN {siren}{', ' + a['tribunal'] if a.get('tribunal') else ''})",
                                  "source": "BODACC (DILA)", "url": BODACC_PAGE.format(id=a["id"]) if a.get("id") else None,
                                  "date_evenement": a["date"]}):
                    rep["nouveaux"] += 1
    if progress:
        progress("BODACC : annonces légales", len(cibles), len(cibles))
    return rep


# --------------------------------------------------------------- changements entre deux mises à jour
def valeurs(sites: dict[str, Record]) -> dict[str, dict]:
    return {sid: dict(r.values) for sid, r in sites.items() if not str(r.get("_origine") or "").startswith("demo")}


PLU_RANG = {"N": 0, "A": 0, "AU": 1, "U": 2}


def changements(avant: dict[str, dict], apres: dict[str, dict], jour: str | None = None) -> list[dict]:
    """Signaux tirés de la comparaison des valeurs d'une friche avant et après une mise à jour."""
    jour = jour or date.today().isoformat()
    out = []

    def sig(sid, typ, niveau, titre, detail, source):
        out.append({"cle": f"chg:{sid}:{typ}:{jour}", "friche_id": sid, "type": typ, "niveau": niveau, "titre": titre,
                    "detail": detail, "source": source, "date_evenement": jour})

    for sid, b in apres.items():
        a = avant.get(sid)
        if a is None:
            continue
        if a.get("icpe_statut") == "en_activite" and b.get("icpe_statut") == "cessee":
            sig(sid, "icpe_cessation", "opportunite", "Cessation d'activité de l'installation classée",
                "L'installation ICPE du site est désormais déclarée en cessation : le site pourrait se libérer.", "Géorisques (ICPE)")
        if not a.get("projet_connu") and b.get("projet_connu"):
            sig(sid, "projet_apparu", "alerte", "Un projet est désormais identifié sur le site",
                "Statut Cartofriches « friche avec projet » : vérifier auprès de la collectivité avant d'engager des études.",
                "Cartofriches")
        pa, pb = a.get("plu_zone"), b.get("plu_zone")
        if pa and pb and pa != pb and pa != "inconnu" and pb != "inconnu":
            mieux = PLU_RANG.get(pb, 0) > PLU_RANG.get(pa, 0)
            sig(sid, "plu_modifie", "opportunite" if mieux else "alerte", f"Zonage du PLU modifié : {pa} → {pb}",
                "La zone constructible s'étend au site." if mieux else "Le site passe dans une zone moins constructible.",
                "Géoportail de l'urbanisme")
        if b.get("zone_humide") == "avere" and a.get("zone_humide") != "avere":
            sig(sid, "zone_humide", "alerte", "Zone humide désormais recensée sur le site",
                "Prévoir une délimitation et, le cas échéant, une compensation.", "Inventaires zones humides")
        if b.get("ppri") == "rouge" and a.get("ppri") != "rouge":
            sig(sid, "ppri_rouge", "alerte", "Site désormais en zone rouge du PPRi", "Construction de logements très contrainte.",
                "Géorisques (PPR)")
        if a.get("statut_cartofriches") == "non_verifiee" and b.get("statut_cartofriches") == "verifiee":
            sig(sid, "friche_verifiee", "info", "Friche vérifiée dans Cartofriches", "Le site a été confirmé par un acteur local.",
                "Cartofriches")
    return out


def enregistrer(signaux: list[dict]) -> int:
    return sum(db.signal_add(s) for s in signaux)


def absentes(liste: list[dict]) -> int:
    jour = date.today().isoformat()
    return enregistrer([{"cle": f"chg:{x['id']}:absente:{jour}", "friche_id": x["id"], "type": "absente_cartofriches",
                         "niveau": "alerte", "titre": "Friche absente de Cartofriches",
                         "detail": "Le site n'apparaît plus dans l'inventaire : requalifié, reconverti ou retiré ? À vérifier.",
                         "source": "Cartofriches", "date_evenement": jour} for x in liste])


def test_bodacc(siren: str) -> dict:
    """Test de connexion : annonces d'un SIREN donné."""
    siren = K.siren_of(siren) or ""
    if not siren:
        return {"ok": False, "erreur": "SIREN attendu : 9 chiffres (ou SIRET de 14 chiffres)"}
    import time
    t0 = time.time()
    try:
        depuis = date.today() - timedelta(days=365 * 10)
        brutes = _bodacc_rows(siren, depuis)
        a = fetch_bodacc(siren, depuis)
        # diagnostic sans nom de personne : familles d'annonces et forme du champ registre
        apercu = [{"date": r.get("dateparution"), "famille": r.get("familleavis_lib") or r.get("familleavis"),
                   "registre": r.get("registre")} for r in brutes[:10] if isinstance(r, dict)]
        return {"ok": True, "source": "BODACC (DILA)", "url": BODACC_URL, "siren": siren, "annonces": a[:20],
                "duree_ms": round((time.time() - t0) * 1000), "requete": _bodacc_query(siren, depuis),
                "resultat": {"annonces_brutes": len(brutes), "annonces_retenues_10_ans": len(a), "annonces": a[:20],
                             "apercu_brut": apercu}}
    except K.ConnectorError as e:
        return {"ok": False, "source": "BODACC (DILA)", "url": BODACC_URL, "erreur": e.message,
                "duree_ms": round((time.time() - t0) * 1000)}
