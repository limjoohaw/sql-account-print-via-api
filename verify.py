"""QR verification signing — HMAC-SHA256 tokens for public invoice verification.

A printed invoice's QR encodes a URL whose signature is computed *inside the SQL
Account report* (FastReport PascalScript) using that company's `verify_secret`. This
module re-creates the identical signature on the server and compares it in constant
time, so a Customer cannot tamper with the DocNo (or any field) to view another
customer's invoice (IDOR protection).

The construction MUST stay byte-for-byte identical to the report script:
    payload   = "<c>|<k>|<n>|<t>"     (company id | doctype key | docno | format/template name)
    signature = hexdigest(HMAC_SHA256(secret, payload))[:SIG_LEN]   (lowercase hex)

Note: `t` is the SQL Account report-format name (e.g. "1. Sales Invoice 8 (SST 2)
HMAC-SHA256"). It is signed (so it can't be swapped) and travels in the URL hex-encoded
(the raw name contains spaces/parentheses). The signature is computed over the RAW name.
"""

import hmac
import hashlib
import secrets

# Number of leading hex chars kept from the HMAC digest. 16 hex = 64 bits, which is
# infeasible to brute-force and keeps the QR small. Must match the report's
# `Copy(HMAC_SHA256(...), 1, 16)`.
SIG_LEN = 16

# Delimiter joining the signed fields. Must match the report's payload builder.
_DELIM = "|"


def sign(company_secret: str, c: str, k: str, n: str, t: str) -> str:
    """Compute the truncated lowercase-hex HMAC-SHA256 signature for a QR link.

    Args:
        company_secret: the company's verify_secret (shared with its report script).
        c: company id        (URL param `c`)
        k: doctype key       (URL param `k`)
        n: document no       (URL param `n`)
        t: report format name, RAW/decoded (URL param `t` carries it hex-encoded)
    """
    payload = _DELIM.join((c, k, n, t))
    digest = hmac.new(
        company_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:SIG_LEN]


def verify(company_secret: str, c: str, k: str, n: str, t: str, sig: str) -> bool:
    """Return True iff `sig` matches the expected signature. Constant-time compare."""
    if not company_secret or not sig:
        return False
    expected = sign(company_secret, c, k, n, t)
    # compare_digest avoids leaking timing information about how many chars matched.
    return hmac.compare_digest(expected, sig.strip().lower())


def generate_verify_secret() -> str:
    """Generate a new random per-company signing secret (64 hex chars / 256 bits)."""
    return secrets.token_hex(32)
