from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .contracts import DataClassification

EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
CANARY_SECRET = re.compile(r"\bCASEOPS_CANARY_[A-Z0-9]{12,64}\b")


class ReleaseDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: Literal["released", "redacted", "blocked"]
    reason_codes: tuple[str, ...]
    released_text: str | None
    original_hash: str
    released_hash: str | None
    detectors: tuple[str, ...]


class OutputGuard:
    def release(
        self,
        text: str,
        *,
        classification: DataClassification,
    ) -> ReleaseDecision:
        original_hash = hashlib.sha256(text.encode()).hexdigest()
        if CANARY_SECRET.search(text):
            return ReleaseDecision(
                effect="blocked",
                reason_codes=("SECRET_CANARY_DETECTED",),
                released_text=None,
                original_hash=original_hash,
                released_hash=None,
                detectors=("canary_secret_v1",),
            )

        detectors: list[str] = []
        released = text
        if EMAIL.search(released):
            detectors.append("email_v1")
            released = EMAIL.sub(lambda match: f"[EMAIL]@{match.group(2)}", released)
        if PHONE.search(released):
            detectors.append("cn_mobile_v1")
            released = PHONE.sub("[PHONE]", released)
        if detectors:
            return ReleaseDecision(
                effect="redacted",
                reason_codes=("PII_MINIMIZED",),
                released_text=released,
                original_hash=original_hash,
                released_hash=hashlib.sha256(released.encode()).hexdigest(),
                detectors=tuple(detectors),
            )
        if classification is DataClassification.RESTRICTED:
            return ReleaseDecision(
                effect="blocked",
                reason_codes=("RESTRICTED_OUTPUT_REQUIRES_EXPLICIT_RELEASE",),
                released_text=None,
                original_hash=original_hash,
                released_hash=None,
                detectors=(),
            )
        return ReleaseDecision(
            effect="released",
            reason_codes=("OUTPUT_POLICY_ALLOW",),
            released_text=released,
            original_hash=original_hash,
            released_hash=original_hash,
            detectors=(),
        )
