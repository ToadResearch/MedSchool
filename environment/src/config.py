from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, find_dotenv


@dataclass(frozen=True)
class AppConfig:
    fhir_base_url: str
    timeout_s: float = 30.0

    @staticmethod
    def _read_env_chain() -> dict[str, str]:
        """
        Load from .env if present; else fall back to .env.example;
        overlay with real process env (process env wins).
        """
        values: dict[str, str] = {}
        # Prefer .env if available
        env_path = find_dotenv(usecwd=True)
        if env_path:
            values |= dotenv_values(env_path, verbose=False, interpolate=True)  # type: ignore[arg-type]
        else:
            example = Path(".env.example")
            if example.exists():
                values |= dotenv_values(str(example), verbose=False, interpolate=True)  # type: ignore[arg-type]

        # Overlay with actual environment
        values |= os.environ  # type: ignore[arg-type]
        # Coerce to str for mypy/typing sanity
        return {k: str(v) for k, v in values.items() if v is not None}

    @classmethod
    def load(cls, *, timeout_s: Optional[float] = None) -> "AppConfig":
        vals = cls._read_env_chain()

        fhir_base = vals.get("FHIR_BASE_URL")
        if not fhir_base and vals.get("FHIR_PROXY_PUBLIC_BASE"):
            fhir_base = f"{vals['FHIR_PROXY_PUBLIC_BASE'].rstrip('/')}/fhir"

        if not fhir_base:
            raise RuntimeError(
                "Could not resolve FHIR_BASE_URL. Set FHIR_BASE_URL in .env or provide FHIR_PROXY_PUBLIC_BASE."
            )

        return cls(
            fhir_base_url=fhir_base.rstrip("/"),
            timeout_s=float(timeout_s) if timeout_s is not None else 30.0,
        )
