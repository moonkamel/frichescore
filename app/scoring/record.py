"""Modèle de donnée tracée.

Chaque indicateur d'une friche est stocké avec sa « fiche de vie » :
source, date de la donnée, méthode et niveau de confiance. Le moteur de score
ne lit jamais une valeur sans pouvoir remonter à sa provenance.

Convention importante : une clé ABSENTE signifie « non renseigné / base non
interrogée ». Une clé présente avec la valeur False ou 0 signifie « base
interrogée, rien trouvé » — ce qui est une information.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

CONFIDENCE_WEIGHT = {"eleve": 1.0, "moyen": 0.75, "faible": 0.45}
MISSING_WEIGHT = 0.3  # indicateur utilisé mais absent : valeur par défaut

# Indicateurs dont l'absence est en elle-même une information (pas de SEVESO,
# pas de programme ACV/PVD...) : leur absence ne dégrade pas la confiance.
OPTIONAL_KEYS = {"seveso", "basol_milieux", "icpe_regime", "icpe_statut", "programme_territorial",
                 "fonds_regional", "pollution_constatee", "bati_annee", "bati_etat"}


@dataclass
class Provenance:
    source: str
    date: str | None = None  # ISO (AAAA-MM-JJ) ou AAAA
    confiance: str = "moyen"  # eleve | moyen | faible
    methode: str | None = None

    def to_dict(self) -> dict:
        return {"source": self.source, "date": self.date, "confiance": self.confiance, "methode": self.methode}


def _parse_year(d: str | None) -> int | None:
    if not d:
        return None
    try:
        return int(str(d)[:4])
    except ValueError:
        return None


def age_years(d: str | None, today: date | None = None) -> float | None:
    y = _parse_year(d)
    if y is None:
        return None
    today = today or date.today()
    return max(0.0, today.year - y + (today.month - 6) / 12)


def age_factor(d: str | None, today: date | None = None) -> float:
    """1.0 jusqu'à 3 ans, décroît à 0.7 à 10 ans, 0.6 au-delà. Date inconnue : 0.8."""
    a = age_years(d, today)
    if a is None:
        return 0.8
    if a <= 3:
        return 1.0
    if a <= 10:
        return 1.0 - 0.3 * (a - 3) / 7
    return 0.6


@dataclass
class Record:
    values: dict[str, Any] = field(default_factory=dict)
    prov: dict[str, Provenance] = field(default_factory=dict)

    # ------------------------------------------------------------------ accès
    def has(self, key: str) -> bool:
        return key in self.values and self.values[key] is not None

    def get(self, key: str, default: Any = None) -> Any:
        v = self.values.get(key)
        return default if v is None else v

    def set(self, key: str, value: Any, prov: Provenance | None = None) -> None:
        self.values[key] = value
        if prov is not None:
            self.prov[key] = prov

    def confidence_of(self, key: str, today: date | None = None) -> float:
        """Confiance effective d'un indicateur (0-1) : niveau × fraîcheur."""
        if not self.has(key):
            return MISSING_WEIGHT
        p = self.prov.get(key)
        if p is None:
            return 0.5
        return CONFIDENCE_WEIGHT.get(p.confiance, 0.6) * age_factor(p.date, today)

    def copy(self) -> "Record":
        return Record(values=dict(self.values), prov=dict(self.prov))

    # --------------------------------------------------------- sérialisation
    def to_dict(self) -> dict:
        return {"values": self.values, "prov": {k: p.to_dict() for k, p in self.prov.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Record":
        prov = {k: Provenance(**p) for k, p in (d.get("prov") or {}).items()}
        return cls(values=dict(d.get("values") or {}), prov=prov)

    def merged_with(self, other: "Record") -> "Record":
        """Retourne une copie où `other` écrase les valeurs (ex. données terrain)."""
        r = self.copy()
        for k, v in other.values.items():
            r.values[k] = v
            if k in other.prov:
                r.prov[k] = other.prov[k]
        return r


class Tracker:
    """Collecte, pendant un calcul, les indicateurs lus (pour la confiance)
    et les contributions au score (pour l'explicabilité)."""

    def __init__(self, rec: Record, today: date | None = None):
        self.rec = rec
        self.today = today
        self.used: dict[str, float] = {}  # clé -> importance
        self.contribs: list[dict] = []
        self.alerts: list[dict] = []

    def read(self, key: str, default: Any = None, importance: float = 1.0) -> Any:
        if key in OPTIONAL_KEYS and not self.rec.has(key):
            return default
        self.used[key] = max(self.used.get(key, 0.0), importance)
        return self.rec.get(key, default)

    def add(self, label: str, delta: float, key: str | None = None) -> float:
        if abs(delta) >= 0.01:
            self.contribs.append({"label": label, "delta": round(delta, 1), "key": key})
        return delta

    def alert(self, level: str, msg: str) -> None:
        self.alerts.append({"niveau": level, "message": msg})

    def confidence(self) -> tuple[float, list[dict]]:
        """Confiance moyenne pondérée + liste des indicateurs les plus faibles."""
        if not self.used:
            return 0.5, []
        tot = sum(self.used.values())
        detail = []
        acc = 0.0
        for k, imp in self.used.items():
            c = self.rec.confidence_of(k, self.today)
            acc += c * imp
            detail.append({"key": k, "confiance": round(c, 2), "importance": imp, "manquant": not self.rec.has(k)})
        detail.sort(key=lambda x: (x["confiance"] - 1) * x["importance"])
        return acc / tot, detail[:4]
