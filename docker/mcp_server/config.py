# mcp_server/config.py
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
import yaml
from pydantic import BaseModel, Field


class ToolLimit(BaseModel):
    max_results: int | None = None
    timeout_s: int | None = Field(default=30, ge=1)


class Settings(BaseModel):
    # ── tool toggles ─────────────────────────────────────────────
    enabled: list[str] = Field(default_factory=list)
    disabled_by_default: list[str] = Field(default_factory=list)

    # ── per-tool limits ─────────────────────────────────────────
    limits: dict[str, ToolLimit] = Field(default_factory=dict)

    # ── auth passthrough ────────────────────────────────────────
    auth_passthrough: bool = True

    # ── FHIR data server (your HAPI instance) ───────────────────
    fhir_base_url: str = Field(
        default_factory=lambda: os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")
    )
    bearer_token: str | None = Field(default_factory=lambda: os.getenv("FHIR_BEARER_TOKEN"))

    # ── external terminology server ─────────────────────────────
    terminology_base_url: str = Field(
        default_factory=lambda: os.getenv("TERMINOLOGY_BASE_URL", "https://tx.fhir.org/r4")
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    yaml_path = Path(__file__).with_name("tools.yaml")
    raw = yaml.safe_load(yaml_path.read_text()) if yaml_path.exists() else {}
    raw["auth_passthrough"] = raw.get("auth", {}).get("passthrough", "bearer") == "bearer"
    return Settings(**raw)
