"""Tests for the Ringmaster auth middleware (AuthManager).

AuthManager is responsible for issuing, verifying, and revoking bearer tokens
used to authenticate clients against the Ringmaster API.  These tests exercise
the full public contract so that any regression in token handling is caught
before it can affect client-facing security.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ringmaster.server.auth import AuthManager


# ---------------------------------------------------------------------------
# Token registration
# ---------------------------------------------------------------------------


class TestRegisterClient:
    def test_register_client_returns_token(self):
        """register() must return a non-empty string token of reasonable length.

        We treat anything shorter than 20 characters as suspiciously weak —
        real tokens are 64 hex characters (32 random bytes).
        """
        mgr = AuthManager()
        token = mgr.register("client-a")
        assert isinstance(token, str)
        assert len(token) > 20

    def test_register_stores_hash_not_plaintext(self):
        """The raw token must never be stored directly — only its hash.

        Storing plaintext tokens would mean a leaked state file exposes all
        credentials.  We verify that the stored value differs from the token.
        """
        mgr = AuthManager()
        token = mgr.register("client-b")
        # clients dict maps client_id → token_hash, not raw token
        stored = mgr.clients["client-b"]
        assert stored != token


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


class TestVerifyToken:
    def test_verify_valid_token(self):
        """verify() must return the client_id for a token issued by register()."""
        mgr = AuthManager()
        token = mgr.register("client-c")
        result = mgr.verify(token)
        assert result == "client-c"

    def test_verify_invalid_token(self):
        """verify() must return None for a token that was never issued."""
        mgr = AuthManager()
        result = mgr.verify("totally-bogus-token")
        assert result is None

    def test_verify_empty_string(self):
        """verify() must return None for an empty string, not raise."""
        mgr = AuthManager()
        result = mgr.verify("")
        assert result is None


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------


class TestRevokeToken:
    def test_revoke_token_invalidates_verification(self):
        """After revoke(), the previously-valid token must no longer verify."""
        mgr = AuthManager()
        token = mgr.register("client-d")
        mgr.revoke("client-d")
        assert mgr.verify(token) is None

    def test_revoke_removes_client_from_registry(self):
        """revoke() must remove the client from the clients dict entirely."""
        mgr = AuthManager()
        mgr.register("client-e")
        mgr.revoke("client-e")
        assert "client-e" not in mgr.clients

    def test_revoke_nonexistent_client_is_noop(self):
        """Revoking a client that was never registered must not raise."""
        mgr = AuthManager()
        mgr.revoke("ghost-client")  # should not raise


# ---------------------------------------------------------------------------
# Re-registration
# ---------------------------------------------------------------------------


class TestRegisterDuplicateClient:
    def test_register_duplicate_client_replaces_old_token(self):
        """Registering a client_id a second time must invalidate the first token.

        This covers the rotation use-case: if a token is compromised, the
        operator re-registers the client to atomically revoke the old token
        and issue a new one.
        """
        mgr = AuthManager()
        old_token = mgr.register("client-f")
        new_token = mgr.register("client-f")

        assert mgr.verify(old_token) is None, "Old token must be revoked after re-registration"
        assert mgr.verify(new_token) == "client-f", "New token must be valid"

    def test_register_duplicate_client_produces_different_token(self):
        """Two successive registrations for the same client must yield different tokens.

        Returning the same token on re-registration would defeat the purpose of
        token rotation.
        """
        mgr = AuthManager()
        t1 = mgr.register("client-g")
        t2 = mgr.register("client-g")
        assert t1 != t2


# ---------------------------------------------------------------------------
# Persistence (save / load)
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    def test_save_and_load_tokens(self, tmp_path: Path):
        """Tokens saved to disk and loaded into a fresh AuthManager must still verify."""
        mgr = AuthManager()
        token = mgr.register("client-h")
        save_path = tmp_path / "tokens.json"
        mgr.save(str(save_path))

        mgr2 = AuthManager()
        mgr2.load(str(save_path))
        assert mgr2.verify(token) == "client-h"

    def test_save_writes_json(self, tmp_path: Path):
        """save() must produce a valid JSON file (not binary or pickle)."""
        mgr = AuthManager()
        mgr.register("client-i")
        save_path = tmp_path / "tokens.json"
        mgr.save(str(save_path))

        with open(save_path) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_load_nonexistent_file_is_noop(self, tmp_path: Path):
        """load() must silently do nothing if the file does not exist.

        On first startup there is no state file, so this must not crash.
        """
        mgr = AuthManager()
        mgr.load(str(tmp_path / "no-such-file.json"))  # must not raise
        assert mgr.clients == {}

    def test_load_rebuilds_reverse_index(self, tmp_path: Path):
        """load() must rebuild the token→client_id reverse index from the saved hashes.

        Without the reverse index, verify() would always return None even after
        a successful load.
        """
        mgr = AuthManager()
        token = mgr.register("client-j")
        save_path = tmp_path / "tokens.json"
        mgr.save(str(save_path))

        mgr2 = AuthManager()
        mgr2.load(str(save_path))
        # Reverse index must be populated so verify() actually works
        assert len(mgr2._tokens) == 1
        assert mgr2.verify(token) == "client-j"
