"""
Authentification par Bearer token — une clé API par site.

La map `{site_id: api_key}` est chargée une fois au démarrage depuis
`SITE_API_KEYS` (variable d'env, JSON). La dépendance FastAPI vérifie :
- présence et format du header `Authorization: Bearer <key>` (401 sinon) ;
- correspondance entre la clé fournie et celle enregistrée pour le
  `site_id` du body (403 sinon).
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, status

from optimizer.config import settings

logger = logging.getLogger(__name__)

_API_KEYS: dict[str, str] = settings.parsed_api_keys()


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header Authorization manquant.",
        )
    parties = authorization.split(None, 1)
    if len(parties) != 2 or parties[0].lower() != "bearer" or not parties[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header Authorization mal formé (attendu : 'Bearer <key>').",
        )
    return parties[1].strip()


def verifier_cle_pour_site(site_id: str, authorization: str | None) -> None:
    """Lève HTTPException si la clé fournie ne correspond pas au site_id."""
    token = _extract_bearer_token(authorization)
    cle_attendue = _API_KEYS.get(site_id)
    if cle_attendue is None or not hmac.compare_digest(token, cle_attendue):
        logger.warning("Auth refusée | site_id=%s", site_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clé API invalide pour ce site.",
        )


def require_bearer(authorization: str | None = Header(default=None)) -> str:
    """Dépendance FastAPI : extrait le token, sans vérifier le site_id."""
    return _extract_bearer_token(authorization)
