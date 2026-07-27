from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CASEOPS_",
        env_file=".env",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    service_name: str = "caseops-api"
    service_version: str = "1.0.0"
    database_url: str = "sqlite+pysqlite:///./caseops.db"
    api_keys: dict[str, str] = Field(
        default_factory=lambda: {"caseops-local-dev-key": "tenant-demo"}
    )
    log_level: str = "INFO"
    expose_metrics: bool = True
    request_default_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    request_max_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    readiness_remote_checks: bool = False
    readiness_timeout_seconds: float = Field(default=1.0, gt=0, le=5)
    expected_database_revision: str = "0007"
    tool_guard_policy_version: str = "caseops.tool-policy.2026-07"
    tool_guard_enabled: bool = True
    otel_exporter_otlp_traces_endpoint: str | None = None
    otel_batch_export_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    agent_planner: Literal["conformance", "openai"] = "conformance"
    agent_tool_transport: Literal["direct", "mcp"] = "direct"
    agent_max_steps: int = Field(default=8, ge=1, le=32)
    agent_repeat_limit: int = Field(default=2, ge=1, le=5)
    agent_tool_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    mcp_url: str = "http://127.0.0.1:8081/mcp"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8081, ge=1, le=65535)
    mcp_resource: str = "http://127.0.0.1:8081/mcp"
    delegation_issuer: str = "https://caseops.local"
    delegation_signing_key: str = "caseops-local-delegation-key-change-me-32-bytes"
    delegation_token_ttl_seconds: int = Field(default=120, ge=30, le=600)
    collaboration_transport: Literal["direct", "a2a"] = "direct"
    collaboration_task_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    a2a_url: str = "http://127.0.0.1:8082"
    a2a_host: str = "127.0.0.1"
    a2a_port: int = Field(default=8082, ge=1, le=65535)
    a2a_resource: str = "http://127.0.0.1:8082/a2a/rest"

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        if self.environment == "production":
            if self.database_url.startswith("sqlite"):
                raise ValueError("production 环境禁止使用 SQLite")
            if "caseops-local-dev-key" in self.api_keys:
                raise ValueError("production 环境禁止使用本地开发 API Key")
            if self.delegation_signing_key.startswith("caseops-local-"):
                raise ValueError("production 环境必须配置独立的任务令牌签名密钥")
        if not self.api_keys:
            raise ValueError("至少配置一个 API Key 到租户的映射")
        if self.request_default_timeout_seconds > self.request_max_timeout_seconds:
            raise ValueError("默认请求超时不能大于最大请求超时")
        if self.agent_planner == "openai" and not self.openai_api_key:
            raise ValueError("使用 openai planner 时必须配置 CASEOPS_OPENAI_API_KEY")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
