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
    service_version: str = "0.1.0"
    database_url: str = "sqlite+pysqlite:///./caseops.db"
    api_keys: dict[str, str] = Field(
        default_factory=lambda: {"caseops-local-dev-key": "tenant-demo"}
    )
    log_level: str = "INFO"
    expose_metrics: bool = True

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        if self.environment == "production":
            if self.database_url.startswith("sqlite"):
                raise ValueError("production 环境禁止使用 SQLite")
            if "caseops-local-dev-key" in self.api_keys:
                raise ValueError("production 环境禁止使用本地开发 API Key")
        if not self.api_keys:
            raise ValueError("至少配置一个 API Key 到租户的映射")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
