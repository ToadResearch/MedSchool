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
        args: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a terminal command in an Alpine Linux sandbox environment.
        
        The sandbox environment includes:
        - Alpine Linux 3.20 base system
        - Bash shell (/bin/bash) as default logged in as user 'sandbox'
        - Python 3 with uv package manager. Do not use pip, uv, apt-get, etc to install new packages.
        - Available Python packages: numpy, pandas, python-dateutil
        - Standard Unix tools: grep
        
        Args:
            command (str): The command to execute (e.g., 'ls', 'python', 'bash', 'curl').
            args (List[str]): List of arguments to pass to the command.
        Returns:
            Dict[str, Any]: Dictionary containing command execution results with keys:
                - 'stdout': Command standard output
                - 'stderr': Command standard error
                - 'exit_code': Command exit code
                - 'success': Boolean indicating if command succeeded (exit code 0)

        Notes:
            - The server automatically wraps non-shell commands as: ["sh","-c","<command and args joined by spaces>"].
              This means pipes, redirects, and && usually work without you explicitly invoking a shell.
            - For precise quoting/globbing, be explicit with: {"command": "bash", "args": ["-c", "<your command>"]}.

        Example tool calls (what to send as the tool's arguments):
            # Run a simple command directly:
            {
              "command": "ls",
              "args": ["-R"]
            }

            # Pipeline relying on the server's 'sh -c' auto-wrap:
            {
              "command": "ls",
              "args": ["-lah | grep 'py$'"]
            }

            # Be explicit for tricky quoting:
            {
              "command": "bash",
              "args": ["-c", "ls -lah | grep 'py$'"]
            }
        """


        ctx = session_manager.require_session(session_id)
        return await ctx.terminal_client.execute_command(
            session_id=session_id,
            command=command,
            args=args or [],
        )


    tools = {}
    if "terminal_command" in _settings.enabled:
        tools["terminal_command"] = terminal_command
    return tools
