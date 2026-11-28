"""Connexion, sessions, rôles et gestion des comptes (mode en ligne, FRICHESCORE_AUTH=1)."""
from __future__ import annotations

import os
import re
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import db, tenant

COOKIE = "fs_session"
SESSION_HEURES = int(os.environ.get("FRICHESCORE_SESSION_HEURES", "12"))
OUVERT = {("POST", "/api/auth/connexion"), ("GET", "/api/auth/moi"), ("POST", "/api/auth/deconnexion"),
          ("POST", "/api/contact")}

# Rôle minimal par route (méthode, motif). Tout le reste : lecteur.
_R = [
    ("admin_plateforme", "PUT", r"/api/maj"), ("admin_plateforme", "POST", r"/api/maj/lancer"),
    ("admin_plateforme", "GET", r"/api/maj"), ("admin_plateforme", "*", r"/api/packs.*"),
    ("admin_plateforme", "POST", r"/api/demo/reset"), ("admin_plateforme", "*", r"/api/organismes.*"),
    ("admin_plateforme", "GET", r"/api/demandes"),
    ("admin", "PUT", r"/api/parametres"), ("admin", "POST", r"/api/parametres/(reset|import)"),
    ("admin", "PUT", r"/api/calendrier"), ("admin", "POST", r"/api/calendrier/reset"), ("admin", "*", r"/api/comptes.*"),
    ("analyste", "PUT", r"/api/portfolio/.+"), ("analyste", "DELETE", r"/api/portfolio/.+"),
    ("analyste", "*", r"/api/sites/[^/]+/terrain.*"), ("analyste", "POST", r"/api/sites/[^/]+/documents.*"),
    ("analyste", "POST", r"/api/import/csv"), ("analyste", "POST", r"/api/ingest/cartofriches"),
    ("analyste", "POST", r"/api/enrichir/sites"), ("analyste", "POST", r"/api/communes/enrichir"),
    ("analyste", "POST", r"/api/ia/(question|jumeau)"), ("analyste", "POST", r"/api/sites/[^/]+/note"),
    ("analyste", "DELETE", r"/api/donnees/.+"), ("analyste", "POST", r"/api/signaux/rechercher"),
    ("analyste", "POST", r"/api/connecteurs/test"),
]
RULES = [(role, meth, re.compile(pat + "$")) for role, meth, pat in _R]


def required_role(method: str, path: str) -> str:
    for role, meth, rx in RULES:
        if (meth == "*" or meth == method) and rx.match(path):
            return role
    return "lecteur"


def allowed(role: str, method: str, path: str) -> bool:
    return tenant.ROLES.get(role, -1) >= tenant.ROLES[required_role(method, path)]


# ---------------------------------------------------------------- limitation des tentatives
_ECHECS: dict[str, list[float]] = {}
MAX_ECHECS, FENETRE_S = 5, 900


def _bloque(cle: str) -> bool:
    t = time.time()
    _ECHECS[cle] = [x for x in _ECHECS.get(cle, []) if t - x < FENETRE_S]
    return len(_ECHECS[cle]) >= MAX_ECHECS


def _echec(cle: str) -> None:
    _ECHECS.setdefault(cle, []).append(time.time())


# ---------------------------------------------------------------- session courante
def user_from_request(request: Request) -> dict | None:
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    return db.session_user(tenant.token_hash(tok))


def public_user(u: dict) -> dict:
    org = db.org_get(u["organisme_id"]) if u.get("organisme_id") else None
    return {"id": u["id"], "email": u["email"], "nom": u.get("nom"), "role": u["role"],
            "role_libelle": tenant.ROLE_LBL.get(u["role"], u["role"]), "organisme": org and {"id": org["id"], "nom": org["nom"]}}


router = APIRouter()


class LoginReq(BaseModel):
    email: str
    mot_de_passe: str


@router.post("/api/auth/connexion")
def connexion(r: LoginReq, request: Request, response: Response):
    if not tenant.auth_enabled():
        raise HTTPException(400, "connexion inutile en mode local")
    ip = request.client.host if request.client else "?"
    cle = f"{r.email.strip().lower()}|{ip}"
    if _bloque(cle):
        raise HTTPException(429, "trop de tentatives : réessayez dans 15 minutes")
    u = db.user_by_email(r.email)
    if not u or not u["actif"] or not tenant.check_password(r.mot_de_passe, u["mdp"]):
        _echec(cle)
        raise HTTPException(401, "adresse ou mot de passe incorrect")
    if u.get("organisme_id"):
        o = db.org_get(u["organisme_id"])
        if not o or not o["actif"]:
            raise HTTPException(403, "l'accès de votre organisme est suspendu")
    _ECHECS.pop(cle, None)
    tok = secrets.token_urlsafe(32)
    exp = datetime.now() + timedelta(hours=SESSION_HEURES)
    db.session_create(tenant.token_hash(tok), u["id"], exp.isoformat(timespec="seconds"))
    response.set_cookie(COOKIE, tok, max_age=SESSION_HEURES * 3600, httponly=True, samesite="lax",
                        secure=os.environ.get("FRICHESCORE_COOKIE_SECURE", "1") == "1", path="/")
    return {"utilisateur": public_user(u)}


@router.post("/api/auth/deconnexion")
def deconnexion(request: Request, response: Response):
    tok = request.cookies.get(COOKIE)
    if tok:
        db.session_delete(tenant.token_hash(tok))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/moi")
def moi(request: Request):
    if not tenant.auth_enabled():
        return {"mode": "local", "utilisateur": None}
    u = user_from_request(request)
    if not u:
        raise HTTPException(401, "connexion requise")
    return {"mode": "en_ligne", "utilisateur": public_user(u)}


class MdpReq(BaseModel):
    actuel: str
    nouveau: str


@router.post("/api/auth/mot-de-passe")
def changer_mdp(r: MdpReq):
    u = tenant.user()
    if not u:
        raise HTTPException(400, "aucun compte en mode local")
    full = db.user_get(u["id"])
    if not tenant.check_password(r.actuel, full["mdp"]):
        raise HTTPException(403, "mot de passe actuel incorrect")
    pb = tenant.password_problem(r.nouveau)
    if pb:
        raise HTTPException(422, pb)
    db.user_update(u["id"], mdp=tenant.hash_password(r.nouveau))  # ferme aussi les sessions ouvertes
    return {"ok": True, "message": "Mot de passe modifié : reconnectez-vous."}


# ---------------------------------------------------------------- comptes
class CompteReq(BaseModel):
    email: str
    nom: str | None = None
    role: str = "analyste"
    mot_de_passe: str
    organisme_id: str | None = None  # administrateur de la plateforme seulement


class ComptePatch(BaseModel):
    nom: str | None = None
    role: str | None = None
    actif: bool | None = None
    mot_de_passe: str | None = None


def _scope_org(requested: str | None) -> str:
    u = tenant.user()
    if u and u["role"] == "admin_plateforme":
        if not requested:
            raise HTTPException(422, "organisme à préciser")
        if not db.org_get(requested):
            raise HTTPException(404, "organisme inconnu")
        return requested
    return tenant.org()


def _check_role(role: str) -> None:
    u = tenant.user()
    if role not in tenant.ROLES:
        raise HTTPException(422, "rôle inconnu (lecteur, analyste, admin)")
    if role == "admin_plateforme" and not (u and u["role"] == "admin_plateforme"):
        raise HTTPException(403, "rôle réservé à la plateforme")


@router.get("/api/comptes")
def comptes(organisme_id: str | None = None):
    u = tenant.user()
    if u and u["role"] == "admin_plateforme" and not organisme_id:
        return {"comptes": db.user_list(), "roles": tenant.ROLE_LBL}
    return {"comptes": db.user_list(_scope_org(organisme_id)), "roles": tenant.ROLE_LBL}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/api/comptes")
def compte_creer(r: CompteReq):
    org = _scope_org(r.organisme_id)
    _check_role(r.role)
    if not EMAIL_RE.match(r.email.strip()):
        raise HTTPException(422, "adresse électronique invalide")
    if db.user_by_email(r.email):
        raise HTTPException(409, "un compte existe déjà avec cette adresse")
    pb = tenant.password_problem(r.mot_de_passe)
    if pb:
        raise HTTPException(422, pb)
    uid = db.user_create(r.email, r.nom, None if r.role == "admin_plateforme" else org, r.role,
                         tenant.hash_password(r.mot_de_passe))
    return {"id": uid, "comptes": db.user_list(org)}


@router.patch("/api/comptes/{uid}")
def compte_modifier(uid: int, r: ComptePatch):
    cible = db.user_get(uid)
    u = tenant.user()
    plateforme = bool(u and u["role"] == "admin_plateforme")
    if not cible or (not plateforme and cible["organisme_id"] != tenant.org()):
        raise HTTPException(404, "compte inconnu")
    f: dict = {}
    if r.nom is not None:
        f["nom"] = r.nom
    if r.role is not None:
        _check_role(r.role)
        f["role"] = r.role
    if r.actif is not None:
        f["actif"] = int(r.actif)
    if r.mot_de_passe:
        pb = tenant.password_problem(r.mot_de_passe)
        if pb:
            raise HTTPException(422, pb)
        f["mdp"] = tenant.hash_password(r.mot_de_passe)
    if u and uid == u["id"] and (f.get("actif") == 0 or ("role" in f and f["role"] != u["role"])):
        raise HTTPException(422, "vous ne pouvez ni désactiver votre propre compte ni changer votre rôle")
    db.user_update(uid, **f)
    return {"comptes": db.user_list(cible["organisme_id"]) if cible["organisme_id"] else db.user_list()}


# ---------------------------------------------------------------- organismes (plateforme)
class OrgReq(BaseModel):
    id: str
    nom: str
    quota_ia: int | None = None


class OrgPatch(BaseModel):
    nom: str | None = None
    actif: bool | None = None
    quota_ia: int | None = None


@router.get("/api/organismes")
def organismes():
    return {"organismes": db.org_list()}


@router.post("/api/organismes")
def organisme_creer(r: OrgReq):
    oid = r.id.strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,39}$", oid) or oid == tenant.LOCAL:
        raise HTTPException(422, "identifiant : 2 à 40 caractères, minuscules, chiffres et tirets (ex. epf-hdf)")
    if db.org_get(oid):
        raise HTTPException(409, "identifiant déjà utilisé")
    db.org_create(oid, r.nom.strip(), r.quota_ia)
    return {"organismes": db.org_list()}


@router.patch("/api/organismes/{oid}")
def organisme_modifier(oid: str, r: OrgPatch):
    if not db.org_get(oid):
        raise HTTPException(404, "organisme inconnu")
    f = {k: (int(v) if isinstance(v, bool) else v) for k, v in r.model_dump().items() if v is not None}
    db.org_update(oid, **f)
    return {"organismes": db.org_list()}


# ---------------------------------------------------------------- premier administrateur (ligne de commande)
def _cli(argv=None):
    import argparse
    import getpass
    ap = argparse.ArgumentParser(description="Comptes FricheScore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("creer-admin", help="crée un administrateur de la plateforme")
    a.add_argument("--email", required=True)
    a.add_argument("--nom")
    o = sub.add_parser("creer-organisme", help="crée un organisme client et son premier administrateur")
    o.add_argument("--id", required=True)
    o.add_argument("--nom", required=True)
    o.add_argument("--admin-email", required=True)
    args = ap.parse_args(argv)
    db.init()

    def ask():
        pw = os.environ.get("FRICHESCORE_MDP_INITIAL") or getpass.getpass("Mot de passe (12 caractères minimum) : ")
        pb = tenant.password_problem(pw)
        if pb:
            raise SystemExit(pb)
        return pw
    if args.cmd == "creer-admin":
        if db.user_by_email(args.email):
            raise SystemExit("un compte existe déjà avec cette adresse")
        db.user_create(args.email, args.nom, None, "admin_plateforme", tenant.hash_password(ask()))
        print(f"Administrateur de la plateforme créé : {args.email}")
    else:
        if not db.org_get(args.id):
            db.org_create(args.id, args.nom)
        db.user_create(args.admin_email, None, args.id, "admin", tenant.hash_password(ask()))
        print(f"Organisme « {args.nom} » ({args.id}) et administrateur {args.admin_email} créés")


if __name__ == "__main__":
    _cli()
