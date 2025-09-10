# environment/src/tools/definitions/terminal.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...config import get_settings

_settings = get_settings()


def register_tools(session_manager):
    """
    Returns a dict of tool_name -> callable.

    Tools use the per-session TerminalClient via session_manager.require_session(session_id).
    """

    async def terminal_command(
        *,
        session_id: str,
        command: str,
        args: Optional[List[str]] = None,
        working_directory: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        user: Optional[str] = None,
        env: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a terminal command in an Alpine Linux sandbox environment.
        
        The sandbox environment includes:
        - Alpine Linux 3.20 base system
        - Bash shell (/bin/bash) as default logged in as user 'sandbox'
        - Python 3 with uv package manager. Do not use pip, uv, apt-get, etc to install new packages.
        - Available Python packages: numpy, pandas, python-dateutil
        - Standard Unix tools: curl, ca-certificates
        
        Args:
            session_id (str): The session ID where the command should be executed.
            command (str): The command to execute (e.g., 'ls', 'python', 'bash', 'curl').
            args (List[str], optional): List of arguments to pass to the command.
            working_directory (str, optional): Directory to execute the command in.
            timeout_seconds (int, optional): Maximum time to wait for command completion.
        
        Returns:
            Dict[str, Any]: Dictionary containing command execution results with keys:
                - 'stdout': Command standard output
                - 'stderr': Command standard error
                - 'exit_code': Command exit code
                - 'success': Boolean indicating if command succeeded (exit code 0)
        """
        ctx = session_manager.require_session(session_id)
        return await ctx.terminal_client.execute_command(
            session_id=session_id,
            command=command,
            args=args or [],
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            user=user,
            env=env,
        )


    tools = {}
    if "terminal_command" in _settings.enabled:
        tools["terminal_command"] = terminal_command
    return tools
