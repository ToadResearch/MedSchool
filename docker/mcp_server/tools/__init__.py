# mcp_server/tools/__init__.py
from importlib import import_module

ALL = {
    "fhir_query": "mcp_server.tools.fhir",
    "fhir_submit_bundle": "mcp_server.tools.fhir",
    "fhir_validate": "mcp_server.tools.fhir",
    "fhir_doc": "mcp_server.tools.fhir",
    "code_lookup": "mcp_server.tools.terminology",
    "snomed_to_icd10": "mcp_server.tools.terminology",
    "icd10_to_snomed": "mcp_server.tools.terminology",
    "notepad_write": "mcp_server.tools.notepad",
    "notepad_read": "mcp_server.tools.notepad",
    "notepad_clear": "mcp_server.tools.notepad",
    "python_exec": "mcp_server.tools.python_exec",
    "shell_exec": "mcp_server.tools.shell_exec",
}


def load(tool_name: str):
    """Import the module that registers the requested tool."""
    mod_path = ALL[tool_name]
    return import_module(mod_path)  # registration side-effect
