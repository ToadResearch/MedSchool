# environment/src/tools/definitions/fhir.py
from __future__ import annotations

from typing import Any, Dict, Optional

from ...config import get_settings

_settings = get_settings()


def _limit_max_results(tool_name: str) -> Optional[int]:
    lim = _settings.limit(tool_name)
    try:
        return int(lim.max_results) if lim and lim.max_results is not None else None
    except Exception:
        return None


def register_tools(session_manager):
    """
    Returns a dict of tool_name -> callable.

    All callables are async and expect `session_id=...` so they can use the
    per-session FHIR client via session_manager.require_session(session_id).
    """
    async def fhir_get(*, session_id: str, path: str) -> Dict[str, Any]:
        """
        HTTP GET / search against the FHIR server.
        
        Args:
            path (str): Accepts anything after the base URL, e.g. 'Patient?name=Smith&_count=5' or 'Observation/123'.
        
        Returns:
            JSON response from the FHIR server.
        """
        ctx = session_manager.require_session(session_id)
        data = await ctx.fhir_client.get_path(path)

        max_results = _limit_max_results("fhir_get")
        if (
            max_results is not None
            and isinstance(data, dict)
            and data.get("resourceType") == "Bundle"
            and isinstance(data.get("entry"), list)
        ):
            data = {**data, "entry": data["entry"][:max_results]}
        return data

    async def fhir_post(*, session_id: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        HTTP POST to the FHIR server.
        
        Args:
            path (str): Relative path after the base URL, e.g. 'Patient' for create,
                        'Observation/$validate', or '' to post a transaction bundle to the base.
            body (dict): JSON payload to send in the POST request.
        
        Returns:
            JSON response from the FHIR server.
        """
        ctx = session_manager.require_session(session_id)
        return await ctx.fhir_client.post_path(path, body)

    async def fhir_update(*, session_id: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        HTTP PUT to the FHIR server (update).
        
        Args:
            path (str): Relative path after the base URL, typically 'ResourceType/{id}', e.g. 'Patient/123'.
            body (dict): JSON payload containing the full updated resource.
        
        Returns:
            JSON response from the FHIR server.
        """
        ctx = session_manager.require_session(session_id)
        return await ctx.fhir_client.put_path(path, body)

    async def fhir_delete(*, session_id: str, path: str) -> Dict[str, Any]:
        """
        HTTP DELETE a resource from the FHIR server.
        
        Args:
            path (str): Relative path after the base URL, typically 'ResourceType/{id}', e.g. 'Observation/456'.
        
        Returns:
            JSON response from the FHIR server (may be an OperationOutcome or a minimal status object).
        """
        ctx = session_manager.require_session(session_id)
        return await ctx.fhir_client.delete_path(path)

    async def fhir_submit_bundle(*, session_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST a FHIR Bundle to the server (transaction).
        
        Args:
            bundle (dict): JSON representation of the FHIR Bundle to submit.
        
        Returns:
            Operation result from the server as a JSON object.
        """
        ctx = session_manager.require_session(session_id)
        return await ctx.fhir_client.post_path("", bundle)

    # TODO: move to validate.py
    async def fhir_validate(*, session_id: str, resource: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate a resource against base profiles via $validate.
        
        Args:
            resource (dict): Raw resource JSON to validate.
        
        Returns:
            OperationOutcome as a JSON object containing validation results.
        """
        rtype = resource.get("resourceType")
        if not rtype:
            return {"error": "Missing 'resourceType' in input JSON"}
        ctx = session_manager.require_session(session_id)
        return await ctx.fhir_client.post_path(f"{rtype}/$validate", resource)

    async def fhir_doc(*, session_id: str, resource_type: str) -> Dict[str, Any]:
        """
        Return a short markdown cheat-sheet for any core R4 resource type.
        
        Args:
            resource_type (str): The FHIR resource type name (e.g., 'Patient', 'Observation').
        
        Returns:
            Markdown formatted documentation for the specified resource type.
        """
        _ = session_id
        docs = {
            "Patient": "### Patient\nKey elements: `identifier`, `name`, `gender`, `birthDate`, …",
            "Observation": "### Observation\nImportant fields: `code`, `value[x]`, `subject`, `effective[x]` …",
        }
        return {"resourceType": resource_type, "doc": docs.get(resource_type, f"No local docs for {resource_type}")}

    tools = {}
    if "fhir_get" in _settings.enabled: tools["fhir_get"] = fhir_get
    if "fhir_post" in _settings.enabled: tools["fhir_post"] = fhir_post
    if "fhir_update" in _settings.enabled: tools["fhir_update"] = fhir_update
    if "fhir_delete" in _settings.enabled: tools["fhir_delete"] = fhir_delete
    if "fhir_submit_bundle" in _settings.enabled: tools["fhir_submit_bundle"] = fhir_submit_bundle
    if "fhir_validate" in _settings.enabled: tools["fhir_validate"] = fhir_validate
    if "fhir_doc" in _settings.enabled: tools["fhir_doc"] = fhir_doc
    return tools
