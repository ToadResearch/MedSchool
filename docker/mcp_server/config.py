# mcp_server/config.py
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

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
    auth_passthrough: bool = False

    # ── FHIR data server (prefer internal proxy → canonical → direct HAPI) ──
    # Expected to resolve to something like:
    #   - http://127.0.0.1:3000/fhir_server/fhir          (host, not for containers)
    #   - http://middleman:3000/fhir_server/fhir          (internal proxy, for containers)
    #   - http://hapi:8080/fhir                           (direct HAPI, for containers)
    fhir_base_url: str = Field(
        default_factory=lambda: (
            (
                # internal proxy base, append /fhir to match FHIR REST root
                os.path.expandvars(os.getenv("FHIR_PROXY_INTERNAL_BASE", ""))
                if os.getenv("FHIR_PROXY_INTERNAL_BASE")
                else ""
            )
            or os.path.expandvars(os.getenv("FHIR_BASE_URL", ""))  # canonical, usually host-facing
            or os.path.expandvars(os.getenv("HAPI_INTERNAL_FHIR_BASE", ""))  # direct HAPI internal
        )
    )

    # ── Terminology server (prefer internal proxy → canonical → remote base) ──
    # Expected to resolve to something like:
    #   - http://127.0.0.1:3000/terminology_server        (host, not for containers)
    #   - http://middleman:3000/terminology_server        (internal proxy, for containers)
    #   - https://tx.fhir.org/r4                          (public HL7 tx server)
    terminology_base_url: str = Field(
        default_factory=lambda: (
            os.path.expandvars(os.getenv("TERMINOLOGY_PROXY_INTERNAL_BASE", ""))
            or os.path.expandvars(os.getenv("TERMINOLOGY_BASE_URL", ""))
            or os.path.expandvars(os.getenv("REMOTE_TS_BASE", ""))
        )
    )

    # ── OpenFDA server (prefer internal proxy → canonical → remote base) ──
    openfda_base_url: str = Field(
        default_factory=lambda: (
            os.path.expandvars(os.getenv("OPENFDA_PROXY_INTERNAL_BASE", ""))
            or os.path.expandvars(os.getenv("OPENFDA_BASE_URL", ""))
            or os.path.expandvars(os.getenv("OPENFDA_UPSTREAM_BASE", ""))
            or "https://api.fda.gov"
        )
    )

    # ── Sandbox server (prefer internal proxy → canonical) ──
    sandbox_base_url: str = Field(
        default_factory=lambda: (
            os.path.expandvars(os.getenv("SANDBOX_PROXY_INTERNAL_BASE", ""))
            or os.path.expandvars(os.getenv("SANDBOX_BASE_URL", ""))
        )
    )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    yaml_path = Path(__file__).with_name("tools.yaml")
    raw = yaml.safe_load(yaml_path.read_text()) if yaml_path.exists() else {}
    raw["auth_passthrough"] = raw.get("auth", {}).get("passthrough", "bearer") == "bearer"
    return Settings(**raw)
