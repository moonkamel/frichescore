"""Appel commun à l'API Claude : réglage réseau, sortie structurée (schéma JSON), repli serveur en cas de refus,
traduction des erreurs en messages lisibles, estimation du coût."""
from __future__ import annotations

import json
import os

MODEL = os.environ.get("FRICHESCORE_IA_MODELE", "claude-opus-5-5")
PRIX_ENTREE = float(os.environ.get("FRICHESCORE_IA_PRIX_ENTREE", "4.0"))   # USD par million de jetons (Claude Opus 5.5)
PRIX_SORTIE = float(os.environ.get("FRICHESCORE_IA_PRIX_SORTIE", "20.0"))


class IAError(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def get_client():
    """Client du SDK officiel avec le même réglage réseau que les connecteurs (IPv4, TLS compact)."""
    import anthropic
    import httpx2

    from ..ingest.connectors import FORCE_IPV4, ssl_context
    transport = httpx2.HTTPTransport(verify=ssl_context(), local_address="0.0.0.0" if FORCE_IPV4 else None)
    return anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(transport=transport), timeout=600, max_retries=2)


def cout_usd(usage: dict) -> float:
    return round(usage.get("entree", 0) / 1e6 * PRIX_ENTREE + usage.get("sortie", 0) / 1e6 * PRIX_SORTIE, 4)


def run_structured(system: str, content: list | str, schema: dict, effort: str = "medium", max_tokens: int = 16000,
                   client=None) -> dict:
    """Une requête, une réponse JSON conforme au schéma. Retourne {resultat, usage, modele}."""
    import anthropic
    if not configured():
        raise IAError("clé d'API Anthropic non configurée (ANTHROPIC_API_KEY dans le fichier .env)")
    client = client or get_client()
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise IAError("clé d'API Anthropic refusée (invalide ou révoquée)")
    except anthropic.PermissionDeniedError as e:
        raise IAError(f"accès refusé par l'API Anthropic : {e.message}")
    except anthropic.RateLimitError:
        raise IAError("limite de débit de l'API Anthropic atteinte : réessayer dans une minute")
    except anthropic.BadRequestError as e:
        raise IAError(f"requête refusée par l'API Anthropic : {e.message}")
    except anthropic.APIStatusError as e:
        raise IAError(f"erreur du service Anthropic (HTTP {e.status_code}) : réessayer plus tard")
    except anthropic.APIConnectionError as e:
        raise IAError(f"connexion à l'API Anthropic impossible : {e}")
    usage = {"entree": msg.usage.input_tokens, "sortie": msg.usage.output_tokens}
    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        raise IAError(f"l'IA a refusé de traiter cette demande (catégorie : {cat or 'non précisée'})")
    if msg.stop_reason == "max_tokens":
        raise IAError("réponse tronquée (trop longue) : réduire la demande ou découper le document")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise IAError("réponse de l'IA illisible (JSON invalide)")
    return {"resultat": data, "usage": usage, "modele": getattr(msg, "model", MODEL)}
