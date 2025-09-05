"""
we want a way to load random records by resource type 
    - also a way to filter or blacklist certain fields / resources within it

or to load a specific resource by it's id. like a specific patient or encounter

"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import verifiers as vf

from .src.config import AppConfig
from .src.fhir_client import AsyncFHIRClient


def load_environment(**kwargs):
    """
    Verifiers entrypoint. Returns a ToolEnv exposing **async** read-only FHIR tools.

    Optional kwargs:
      - timeout_s: float (HTTP timeout seconds, default 30)
      - system_prompt: override default
    """
    timeout_s = float(kwargs.get("timeout_s", 30.0))
    cfg = AppConfig.load(timeout_s=timeout_s)
    client = AsyncFHIRClient(cfg.fhir_base_url, timeout_s=cfg.timeout_s)

    # ---- Async tool functions ------------------------------------------

    async def get_capability() -> Dict[str, Any]:
        """Return the server CapabilityStatement (read-only)."""
        return await client.get_capability()

    async def read_resource(resource_type: str, resource_id: str) -> Dict[str, Any]:
        """
        Read a single resource by type and id.
        Example: read_resource("Patient", "123")
        """
        return await client.read(resource_type, resource_id)

    async def search_resources(
        resource_type: str, params: Optional[Mapping[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Search by type with arbitrary FHIR search params (e.g., {"name":"Smith","_count":20}).
        Returns a Bundle.
        """
        return await client.search(resource_type, params)

    async def count_resources(resource_type: str, params: Optional[Mapping[str, Any]] = None) -> int:
        """Return total matching resources (uses _summary=count)."""
        return await client.count(resource_type, params)

    async def sample_resource(
        resource_type: str, params: Optional[Mapping[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Return one random resource matching the (optional) params.
        Example: sample_resource("Observation", {"code": "http://loinc.org|718-7"})
        """
        return await client.sample(resource_type, params)

    tools = [get_capability, read_resource, search_resources, count_resources, sample_resource]

    # Parser & rubric (lightweight; tools do the heavy lifting)
    parser = vf.PlainParser()
    rubric = vf.Rubric(funcs=[parser.get_format_reward_func()], weights=[0.1])

    system_prompt = kwargs.get(
        "system_prompt",
        (
            "You can query a FHIR R4 server using async, read-only tools. "
            "Use the tools to fetch, search, count, and sample resources. "
            "Summarize key clinical fields succinctly."
        ),
    )

    # Minimal seed dataset; you can ignore and just call tools programmatically.
    dataset = vf.Dataset.from_list(
        [{"prompt": "Fetch a random Patient and list their id, name(s), birthDate, and gender."}]
    )

    return vf.ToolEnv(
        dataset=dataset,
        tools=tools,      # async callables are supported
        parser=parser,
        rubric=rubric,
        system_prompt=system_prompt,
        **{k: v for k, v in kwargs.items() if k not in {"timeout_s", "system_prompt"}}
    )
