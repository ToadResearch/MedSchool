# environment/src/tools/definitions/fhir.py
from __future__ import annotations

import json
import base64
from typing import Any, Dict, Optional

from ...config import get_settings
from .utils import (
    _resource_counts, 
    _top_level_keys, 
    _per_resource_type_keys,
    _identifier_for_bundle,
    _identifier_from_resource,
    _json_minified,
    _json_pretty,
    _counts,
    _save_path,
    _short_hash,
    _write_file_to_container
)

_settings = get_settings()


def _limit_max_results(tool_name: str) -> Optional[int]:
    lim = _settings.limit(tool_name)
    try:
        return int(lim.max_results) if lim and lim.max_results is not None else None
    except Exception:
        return None

# TODO: treating payloads as str in fhir_validate, fhir_post, fhir_update for now to avoid Pydantic validation issues. Used to use Dict[str, Any] but wouldn't work. figure this out later
def _parse_json_object(s: str, field_name: str) -> Dict[str, Any] | Dict[str, str]:
    """
    Parse a JSON string into a dict. Return an error object if invalid or not an object.
    """
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in '{field_name}': {e.msg} at pos {e.pos}"}
    if not isinstance(data, dict):
        return {"error": f"'{field_name}' must be a JSON object"}
    return data

def register_tools(session_manager):
    """
    Returns a dict of tool_name -> callable.

    All callables are async and expect `session_id=...` so they can use the
    per-session FHIR client via session_manager.require_session(session_id).
    """
    async def fhir_get(*, session_id: str, request: str, save: bool = True, save_path: str = None) -> Dict[str, Any]:
        """
        GET/search the FHIR server and return a **preview** (no bulky JSON).
        Also auto-saves the full JSON (by default) to a **relative** path:
          fhir/<resourceType>/<identifier>.json

        Args:
            request: Anything after the base URL, e.g. 'Patient/123' or 'Patient?name=Smith&_count=5'
            save: If true (default), write the full JSON file.
            save_path: Optional custom path (relative or absolute). If omitted, the default pattern is used.

        Returns (preview only; full JSON is not returned):
            {
              "kind": "preview",
              "request": "Patient?name=Smith&_count=5",
              "resourceType": "Bundle" | "<Type>" | null,
              "bundle": { "type": "...", "total": int, "entry_count": int, "resource_counts": {...} },   # if Bundle
              "schema": {
                "top_level_keys": [...],
                "per_resource_type": {...}   # only for Bundle
              },
              "size": {"bytes": int, "lines": int},
              "saved": {"path": "fhir/Bundle/patient-name-smith_XXXXXXXXXX.json", "bytes": int}  # if save=True
            }
        """
        ctx = session_manager.require_session(session_id)

        data = await ctx.fhir_client.get_path(request)

        # TODO: now that we're saving to the sandbox, we can probably remove limits (might want for full search results)
        max_results = _limit_max_results("fhir_get")
        if (
            max_results is not None
            and isinstance(data, dict)
            and data.get("resourceType") == "Bundle"
            and isinstance(data.get("entry"), list)
        ):
            data = {**data, "entry": data["entry"][:max_results]}

        resource_type = data.get("resourceType") if isinstance(data, dict) else None

        preview: Dict[str, Any] = {
            "kind": "preview",
            "request": request,
            "resourceType": resource_type,
        }
        
        if isinstance(data, dict) and resource_type == "Bundle":
            preview["bundle"] = {
                "type": data.get("type"),
                "total": data.get("total"),
                "entry_count": len(data.get("entry") or []),
                "resource_counts": _resource_counts(data),
            }
            preview["schema"] = {
                "top_level_keys": _top_level_keys(data),
                "per_resource_type": _per_resource_type_keys(data),
            }
            identifier = _identifier_for_bundle(request)
        elif isinstance(data, dict):
            preview["schema"] = {"top_level_keys": _top_level_keys(data)}
            identifier = _identifier_from_resource(data)
        else:
            preview["schema"] = {"top_level_keys": []}
            identifier = f"h{_short_hash(_json_minified(data if data is not None else {}))}"

        # Size based on pretty print, purely for human reference
        s_pretty = _json_pretty(data)
        bytes_utf8, line_count = _counts(s_pretty)
        preview["size"] = {"bytes": bytes_utf8, "lines": line_count}

        # Save file (relative path) if requested
        if save:
            file_path = _save_path(resource_type, identifier, save_path)
            s_min = _json_minified(data)
            b64 = base64.b64encode(s_min.encode("utf-8")).decode("ascii")
            write_res = await _write_file_to_container(
                session_manager=session_manager,
                session_id=session_id,
                file_path=file_path,
                content_b64=b64,
            )
            # Try to parse size from stdout; fall back to local byte count
            written = bytes_utf8
            try:
                out = (write_res.get("stdout") or "").strip()
                if out.isdigit():
                    written = int(out)
            except Exception:
                pass
            preview["saved"] = {"path": file_path, "bytes": written}

        return preview

    async def fhir_post(*, session_id: str, path: str, body_json: Dict[str, Any]) -> Dict[str, Any]:
        """
        HTTP POST to the FHIR server.
        
        Args:
            path (str): Relative path after the base URL, e.g. 'Patient' for create,
                        'Observation/$validate', or '' to post a transaction bundle to the base.
            body_json (str): JSON string for the request body to send in the POST request (must be an object).
        
        Returns:
            JSON response from the FHIR server.
        """
        ctx = session_manager.require_session(session_id)
        body = _parse_json_object(body_json, "body_json")
        if "error" in body:
            return body
        return await ctx.fhir_client.post_path(path, body)

    async def fhir_update(*, session_id: str, path: str, body_json: str) -> Dict[str, Any]:
        """
        HTTP PUT to the FHIR server (update).
        
        Args:
            path (str): Relative path after the base URL, typically 'ResourceType/{id}', e.g. 'Patient/123'.
            body (str): JSON string payload containing the full updated resource.
        
        Returns:
            JSON response from the FHIR server.
        """
        ctx = session_manager.require_session(session_id)
        body = _parse_json_object(body_json, "body_json")
        if "error" in body:
            return body
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

    async def fhir_submit_bundle(*, session_id: str, bundle: str) -> Dict[str, Any]:
        """
        POST a FHIR Bundle to the server (transaction).
        
        Args:
            bundle (str): JSON representation of the FHIR Bundle to submit.
        
        Returns:
            Operation result from the server as a JSON object.
        """
        ctx = session_manager.require_session(session_id)
        return await ctx.fhir_client.post_path("", bundle)

    async def fhir_validate(*, session_id: str, resource_json: str) -> Dict[str, Any]:
        """
        Validate a resource against base profiles via $validate.
        
        Args:
            resource (str): Raw resource JSON to validate.
        
        Returns:
            OperationOutcome as a JSON object containing validation results.
        """
        ctx = session_manager.require_session(session_id)
        resource = _parse_json_object(resource_json, "resource_json")
        if "error" in resource:
            return resource
        rtype = resource.get("resourceType")
        if not isinstance(rtype, str) or not rtype:
            return {"error": "Missing 'resourceType' in resource_json"}
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
