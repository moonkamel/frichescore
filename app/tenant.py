"""Organismes (clients), utilisateurs et contexte d'exécution.

Deux modes :
- **local** (par défaut, FRICHESCORE_AUTH absent ou 0) : un seul organisme implicite « local », pas de connexion ;
  c'est le fonctionnement historique sur un poste.
- **en ligne** (FRICHESCORE_AUTH=1) : chaque requête porte une session ; l'organisme de l'utilisateur détermine les
  données privées visibles (friches importées, saisies terrain, portfolio, documents, notes IA, paramètres,
  calendrier, historique, signaux privés). Les friches Cartofriches, les indicateurs communaux et les signaux
  publics forment la base commune, mise à jour la nuit pour tous.

L'organisme courant est porté par une variable de contexte : il suit la requête jusque dans les fils d'exécution
de FastAPI et dans les tâches de fond (voir jobs.start_job). Hors requête, le contexte est « système » : base
commune seule (mise à jour nocturne, ligne de commande).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
from contextlib import contextmanager
from contextvars import ContextVar

LOCAL = "local"
PLATEFORME = "_plateforme"  # espace de travail de l'administrateur de la plateforme : base commune + son propre bac à sable
ROLES = {"lecteur": 0, "analyste": 1, "admin": 2, "admin_plateforme": 3}
ROLE_LBL = {"lecteur": "Lecteur", "analyste": "Analyste", "admin": "Administrateur de l'organisme",
            "admin_plateforme": "Administrateur de la plateforme"}

_org: ContextVar[str | None] = ContextVar("frichescore_org", default=None)
_user: ContextVar[dict | None] = ContextVar("frichescore_user", default=None)


def auth_enabled() -> bool:
    return os.environ.get("FRICHESCORE_AUTH", "0") == "1"


def org() -> str | None:
    """Organisme courant ; None = contexte système (base commune seule). En mode local : toujours « local »."""
    v = _org.get()
    if v is None and not auth_enabled():
        return LOCAL
    return v


def user() -> dict | None:
    return _user.get()


def set_context(org_id: str | None, usr: dict | None = None):
    return _org.set(org_id), _user.set(usr)


def reset_context(tokens) -> None:
    _org.reset(tokens[0])
    _user.reset(tokens[1])


@contextmanager
def as_org(org_id: str | None, usr: dict | None = None):
    t = set_context(org_id, usr)
    try:
        yield
    finally:
        reset_context(t)


# ---------------------------------------------------------------- mots de passe
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(pw: str, stored: str | None) -> bool:
    try:
        algo, s, h = (stored or "").split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(pw.encode("utf-8"), salt=base64.b64decode(s), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def password_problem(pw: str) -> str | None:
    if len(pw or "") < 12:
        return "le mot de passe doit compter au moins 12 caractères"
    if pw.lower() == pw or pw.upper() == pw or not any(ch.isdigit() for ch in pw):
        return "le mot de passe doit mêler majuscules, minuscules et chiffres"
    return None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- paramètres par organisme
class _ParamContext:
    """Les paramètres du moteur sont appliqués EN PLACE sur les modules de configuration (voir scoring/params.py).
    Plusieurs requêtes du même organisme peuvent calculer en même temps ; une requête d'un autre organisme attend
    qu'elles aient fini, puis applique ses propres paramètres. En mode local, il n'y a jamais d'attente."""

    def __init__(self):
        self.cond = threading.Condition()
        self.active: object = object()
        self.count = 0
        self.dirty: set = set()

    @contextmanager
    def use(self, org_id: str | None):
        with self.cond:
            while self.count > 0 and self.active != org_id:
                self.cond.wait()
            if self.active != org_id or org_id in self.dirty:
                self._apply(org_id)
                self.active = org_id
                self.dirty.discard(org_id)
            self.count += 1
        try:
            yield
        finally:
            with self.cond:
                self.count -= 1
                self.cond.notify_all()

    def _apply(self, org_id):
        from . import db
        from .scoring import params
        with as_org(org_id):
            params.apply({p: o["value"] for p, o in db.param_overrides().items()})

    def invalidate(self, org_id: str | None):
        """Après une modification des paramètres d'un organisme (appelé pendant que cet organisme est actif)."""
        with self.cond:
            if self.active == org_id:
                self._apply(org_id)
            else:
                self.dirty.add(org_id)


PARAMS = _ParamContext()
