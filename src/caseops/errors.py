from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CaseOpsError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class CaseNotFound(CaseOpsError):
    def __init__(self, case_id: str) -> None:
        super().__init__(
            code="CASE_NOT_FOUND",
            message=f"案件 {case_id} 不存在。",
            details={"case_id": case_id},
        )


class PolicyNotFound(CaseOpsError):
    def __init__(self, policy_id: str, version: str) -> None:
        super().__init__(
            code="POLICY_NOT_FOUND",
            message="找不到案件绑定的规则版本。",
            details={"policy_id": policy_id, "version": version},
        )


class ActionNotAllowed(CaseOpsError):
    def __init__(self, requested_action: str) -> None:
        super().__init__(
            code="ACTION_NOT_ALLOWED",
            message="Slice 0 只允许生成通知草稿，不允许直接产生外部副作用。",
            details={
                "requested_action": requested_action,
                "allowed_action": "draft",
            },
        )


class DataContractError(CaseOpsError):
    def __init__(self, source: str, reason: str) -> None:
        super().__init__(
            code="DATA_CONTRACT_ERROR",
            message=f"数据源 {source} 不符合契约。",
            details={"source": source, "reason": reason},
        )


class IdempotencyConflict(CaseOpsError):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            code="IDEMPOTENCY_KEY_REUSED",
            message="同一幂等键已用于不同的请求。",
            details={"idempotency_key": idempotency_key},
        )


class SystemRunNotFound(CaseOpsError):
    def __init__(self, system_run_id: str) -> None:
        super().__init__(
            code="SYSTEM_RUN_NOT_FOUND",
            message="系统运行不存在或当前租户无权访问。",
            details={"system_run_id": system_run_id},
        )
