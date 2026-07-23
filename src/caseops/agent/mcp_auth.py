from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from mcp.server.auth.provider import AccessToken, TokenVerifier

from caseops.config import Settings
from caseops.service import Principal


@dataclass(frozen=True, slots=True)
class DelegationTokenIssuer:
    settings: Settings

    def issue(self, *, principal: Principal, task_id: str) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.settings.delegation_issuer,
            "aud": self.settings.mcp_resource,
            "sub": principal.actor_id,
            "tenant_id": principal.tenant_id,
            "task_id": task_id,
            "scope": " ".join(sorted(principal.scopes)),
            "iat": now,
            "nbf": now - 5,
            "exp": now + self.settings.delegation_token_ttl_seconds,
            "jti": str(uuid4()),
        }
        return _encode_hs256(claims, self.settings.delegation_signing_key)


@dataclass(frozen=True, slots=True)
class DelegationTokenVerifier(TokenVerifier):
    settings: Settings

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = _decode_hs256(
                token,
                self.settings.delegation_signing_key,
                audience=self.settings.mcp_resource,
                issuer=self.settings.delegation_issuer,
            )
        except (KeyError, TypeError, ValueError):
            return None
        scopes = str(claims.get("scope", "")).split()
        return AccessToken(
            token=token,
            client_id=str(claims["sub"]),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.settings.mcp_resource,
            subject=str(claims["sub"]),
            claims=claims,
        )


def _base64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _base64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)


def _encode_hs256(claims: dict[str, Any], key: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _base64url_encode(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    )
    encoded_claims = _base64url_encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    )
    signing_input = f"{encoded_header}.{encoded_claims}"
    signature = hmac.new(
        key.encode(),
        signing_input.encode(),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_base64url_encode(signature)}"


def _decode_hs256(
    token: str,
    key: str,
    *,
    audience: str,
    issuer: str,
) -> dict[str, Any]:
    encoded_header, encoded_claims, encoded_signature = token.split(".")
    header = json.loads(_base64url_decode(encoded_header))
    if header != {"alg": "HS256", "typ": "JWT"}:
        raise ValueError("unexpected task token header")
    signing_input = f"{encoded_header}.{encoded_claims}"
    expected = hmac.new(
        key.encode(),
        signing_input.encode(),
        hashlib.sha256,
    ).digest()
    supplied = _base64url_decode(encoded_signature)
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("invalid task token signature")
    claims_raw: Any = json.loads(_base64url_decode(encoded_claims))
    if not isinstance(claims_raw, dict):
        raise ValueError("task token claims must be an object")
    claims: dict[str, Any] = {str(claim): value for claim, value in claims_raw.items()}
    required = {"exp", "iat", "nbf", "sub", "tenant_id", "task_id", "iss", "aud"}
    if not required.issubset(claims):
        raise ValueError("task token lacks required claims")
    now = int(time.time())
    if claims["iss"] != issuer or claims["aud"] != audience:
        raise ValueError("task token issuer or audience mismatch")
    if int(claims["nbf"]) > now + 5 or int(claims["exp"]) <= now:
        raise ValueError("task token is not currently valid")
    if int(claims["iat"]) > now + 5:
        raise ValueError("task token issued-at is in the future")
    return claims
