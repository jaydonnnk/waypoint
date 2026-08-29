"""Shared release-credential KDF (S5 extraction of the S4 helpers).

These three names were born in `app/api/routes.py` (S1, hardened in S4 and
S4-review). S5 adds a SECOND manager credential — the per-round approval
token minted by the agent loop at the pre-trip approval checkpoint — and
the loop cannot import the routes module (routes imports the loop). So the
KDF lives here and BOTH callers import it; `routes` re-exports the private
aliases (`_hash_code`, `_verify_code`, `_KDF_ITERATIONS`) so its existing
call sites and the S4 security tests keep working unchanged.

Nothing about the scheme changed in the move: PBKDF2-HMAC-SHA256 with the
iteration count stored IN the hash string, constant-time compare, legacy
single-round `salt$digest` still verifying, and a fail-closed bounds check
on the stored iteration count.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

KDF_ITERATIONS = 260_000  # OWASP 2023 PBKDF2-SHA256 minimum

# Fail-closed bounds on a STORED iteration count (M-new1): 0/negative would
# verify at zero cost (a forged hash beats the KDF); a huge count is a CPU
# bomb pointed at the verify path.
_MIN_STORED_ITERS = 1
_MAX_STORED_ITERS = 1_000_000


def hash_code(code: str) -> str:
    """Slow KDF hash of a credential -> 'pbkdf2$<iters>$salt$digest'.

    Plaintext is NEVER stored. The iteration count is stored IN the string
    (L3) so tuning KDF_ITERATIONS later never makes an at-rest hash
    unverifiable. Scheme-tagged so old hashes still verify (back-compat):
    the S1 single-round 'salt$digest' SHA-256 form.
    """
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", code.encode(), salt.encode(), KDF_ITERATIONS
    ).hex()
    return f"pbkdf2${KDF_ITERATIONS}${salt}${digest}"


def verify_code(code: str, stored: str | None) -> bool:
    """Constant-time check of a plaintext credential against a stored hash.

    Scheme-tagged, back-compat across the at-rest formats:
    - 'pbkdf2$<iters>$salt$digest' (current) — iterations parsed FROM the
      stored value, never trusted from the module constant, and fail-closed
      on any out-of-range stored count (M-new1).
    - 'salt$digest' (legacy S1 single-round SHA-256).
    All paths use hmac.compare_digest for constant-time comparison.
    """
    if not stored or "$" not in stored:
        return False
    if stored.startswith("pbkdf2$"):
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        # pbkdf2$<iters>$salt$digest — iterations FROM the stored value.
        _, iters_raw, salt, digest = parts
        try:
            iters = int(iters_raw)
        except ValueError:
            return False
        if not (_MIN_STORED_ITERS <= iters <= _MAX_STORED_ITERS):
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", code.encode(), salt.encode(), iters
        ).hex()
        return hmac.compare_digest(candidate, digest)
    # Legacy S1 scheme: salt$digest (single-round SHA-256, back-compat)
    salt, digest = stored.split("$", 1)
    candidate = hashlib.sha256(f"{salt}{code}".encode()).hexdigest()
    return hmac.compare_digest(candidate, digest)
