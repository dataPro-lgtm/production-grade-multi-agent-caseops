from __future__ import annotations

from caseops.api.app import create_app
from caseops.config import Settings


def test_openapi_exposes_versioned_investigation_contract() -> None:
    app = create_app(
        settings=Settings(
            environment="test",
            database_url="sqlite+pysqlite://",
            api_keys={"contract-test-key": "tenant-demo"},
            log_level="WARNING",
        )
    )

    schema = app.openapi()
    operation = schema["paths"]["/v1/cases/{case_id}/investigations"]["post"]
    assert operation["responses"]["201"]["content"]["application/json"]
    assert operation["responses"]["409"]["content"]["application/json"]
    assert "Idempotency-Key" in {parameter["name"] for parameter in operation["parameters"]}
