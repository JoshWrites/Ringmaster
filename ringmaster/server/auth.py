"""Token-based authentication middleware for the Ringmaster API.

Clients are identified by a stable ``client_id`` string (e.g. "workstation-1")
and authenticate via a bearer token issued at registration time.  Tokens are
never stored in plaintext — only their SHA-256 hash is kept in memory and on
disk — so a leaked state file does not expose live credentials.

Typical lifecycle
-----------------
1. An operator calls ``register(client_id)`` once and distributes the raw token
   to the client out-of-band.
2. Each API request supplies the token in an ``Authorization: Bearer <token>``
   header.  The server calls ``verify(token)`` to authenticate the request.
3. If a token is compromised the operator calls ``register(client_id)`` again,
   which atomically revokes the old token and issues a new one.
4. ``save`` / ``load`` let the registry survive a server restart without
   requiring clients to re-register — only the hashes are persisted.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path


class AuthManager:
    """Registry of client IDs and their associated bearer-token hashes.

    Two internal dictionaries form a bidirectional index:

    * ``clients`` maps ``client_id → token_hash`` — the authoritative record.
    * ``_tokens`` maps ``token_hash → client_id`` — a reverse index that makes
      ``verify()`` O(1) without scanning all clients.

    Neither dictionary ever holds a raw (unhashed) token after ``register``
    returns.
    """

    def __init__(self) -> None:
        # client_id → token_hash (authoritative store, persisted to disk)
        self.clients: dict[str, str] = {}
        # token_hash → client_id (reverse index, rebuilt from clients on load)
        self._tokens: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_token(token: str) -> str:
        """Return the SHA-256 hex digest of *token*.

        Using a fast, collision-resistant hash is sufficient here because the
        tokens themselves are 256-bit random values; there is no need for a
        slow KDF (bcrypt/argon2) since brute-forcing a 256-bit random token is
        computationally infeasible regardless of hash speed.
        """
        return hashlib.sha256(token.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, client_id: str) -> str:
        """Issue a new bearer token for *client_id* and return the raw token.

        If *client_id* already has a token, the old token is revoked first so
        that exactly one valid token exists for each client at any time.  This
        makes ``register`` safe to call for token rotation.

        Args:
            client_id: The stable identifier for the client (e.g. "gpu-node-1").

        Returns:
            A 64-character hex string (32 random bytes) that the client must
            present in ``Authorization: Bearer`` headers.  The caller is
            responsible for distributing this token securely — it is never
            stored and cannot be recovered after this call returns.
        """
        # Revoke any existing token so the reverse index stays consistent.
        if client_id in self.clients:
            old_hash = self.clients[client_id]
            self._tokens.pop(old_hash, None)

        raw_token = secrets.token_hex(32)  # 256 bits of entropy → 64 hex chars
        token_hash = self._hash_token(raw_token)

        self.clients[client_id] = token_hash
        self._tokens[token_hash] = client_id

        return raw_token

    def verify(self, token: str) -> str | None:
        """Return the *client_id* for *token*, or ``None`` if unrecognised.

        The lookup is O(1) via the reverse index.  An unknown or revoked token
        returns ``None`` rather than raising, so callers can treat the return
        value as a simple authenticated identity (or lack thereof).

        Args:
            token: The raw bearer token presented by the client.

        Returns:
            The ``client_id`` that owns this token, or ``None``.
        """
        token_hash = self._hash_token(token)
        return self._tokens.get(token_hash)

    def revoke(self, client_id: str) -> None:
        """Remove *client_id* and its token from the registry.

        After revocation, any token previously issued to this client will fail
        ``verify()``.  Calling ``revoke`` on an unknown client is a no-op.

        Args:
            client_id: The client whose access should be terminated.
        """
        token_hash = self.clients.pop(client_id, None)
        if token_hash is not None:
            self._tokens.pop(token_hash, None)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Persist the ``clients`` registry to *path* as a JSON file.

        Only token hashes are written — raw tokens are never persisted.  The
        file can therefore be stored alongside other server state without
        creating a credential-exposure risk.

        Args:
            path: Filesystem path for the output file.  Parent directory must
                  exist.
        """
        Path(path).write_text(json.dumps(self.clients, indent=2))

    def load(self, path: str) -> None:
        """Populate the registry from a JSON file previously written by ``save``.

        Both ``clients`` and the ``_tokens`` reverse index are rebuilt from the
        file contents, so ``verify()`` works immediately after ``load()``.
        Silently does nothing if *path* does not exist, allowing the server to
        start cleanly on first run.

        Args:
            path: Filesystem path of the JSON file to read.
        """
        p = Path(path)
        if not p.exists():
            return

        data: dict[str, str] = json.loads(p.read_text())
        self.clients = data
        # Rebuild the reverse index from the loaded client→hash mapping.
        self._tokens = {token_hash: client_id for client_id, token_hash in data.items()}
