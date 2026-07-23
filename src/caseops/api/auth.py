from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, Request

from caseops.errors import CaseOpsError
from caseops.service import Principal


class AuthenticationFailed(CaseOpsError):
    def __init__(self) -> None:
        super().__init__(
            code="AUTHENTICATION_FAILED",
            message="缺少或无效的 API Key。",
        )


def authenticate(
    request: Request,
    api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    if api_key is None:
        raise AuthenticationFailed()

    settings = request.app.state.settings
    for candidate, tenant_id in settings.api_keys.items():
        if hmac.compare_digest(api_key, candidate):
            fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
            return Principal(
                tenant_id=tenant_id,
                actor_id=f"api-key:{fingerprint}",
            )
    raise AuthenticationFailed()
