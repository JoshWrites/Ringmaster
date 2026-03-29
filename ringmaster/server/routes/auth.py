"""Auth management route handlers — register and revoke API client tokens.

These endpoints let operators manage the bearer-token registry over the API
itself.  This creates a bootstrapping requirement: you must already have a
valid token to call these endpoints.  In practice, the initial token is issued
at server setup time (via the CLI) and subsequent registrations use that token.

Token security model:
  - Tokens are never stored in plaintext — only their SHA-256 hash lives in
    the AuthManager and the on-disk state file.
  - The raw token is returned exactly once (at registration time) and is never
    recoverable after that call returns.  Operators must note it down securely.
  - Re-registering an existing client_id atomically revokes the old token and
    issues a new one, so this is also the token-rotation mechanism.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends

from ringmaster.server.auth import AuthManager
from ringmaster.server.deps import get_auth_manager

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request models (auth-specific, not in the shared models.py)
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """Body of POST /auth/register."""

    client_id: str = Field(
        description="Stable identifier for the client being registered, e.g. 'gpu-node-1'.",
    )


class RevokeRequest(BaseModel):
    """Body of POST /auth/revoke."""

    client_id: str = Field(
        description="Identifier of the client whose access should be terminated.",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/register")
def register_client(
    body: RegisterRequest,
    auth: AuthManager = Depends(get_auth_manager),
) -> dict:
    """Issue a new bearer token for the given client_id.

    If the client already has a token, the old token is revoked atomically
    and a new one is issued — this doubles as the token-rotation endpoint.

    The returned token is the only time the raw value will ever be visible;
    it is not stored anywhere.  The caller must distribute it securely.
    """
    raw_token = auth.register(body.client_id)
    return {"client_id": body.client_id, "token": raw_token}


@router.post("/revoke")
def revoke_client(
    body: RevokeRequest,
    auth: AuthManager = Depends(get_auth_manager),
) -> dict:
    """Revoke all tokens for the given client_id.

    After revocation, any token previously issued to this client will fail
    authentication.  Revoking an unknown client_id is a no-op (idempotent).
    """
    auth.revoke(body.client_id)
    return {"client_id": body.client_id, "revoked": True}
