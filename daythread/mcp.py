"""Minimal MCP stdio client and server registry."""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, obs
from .db import canonical, get_setting, json_load, row_to_dict, utc_now
from .errors import McpError, ValidationError

DEFAULT_TIMEOUT = 10.0
DEFAULT_ALLOWED_COMMANDS = "python,python3,node,npx,uvx"
BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "COMSPEC", "PATHEXT")


class McpStdioClient:
    def __init__(
        self,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[Path] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not command:
            raise ValidationError("MCP command is empty")
        self.command = command
        self.env = env or {}
        self.cwd = str(cwd) if cwd else None
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0

    def __enter__(self) -> "McpStdioClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self.proc is not None:
            return
        env = _base_env()
        env.update(self.env)
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=self.cwd,
            env=env,
        )

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        finally:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
            self.proc = None

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise McpError("MCP client is not running")
        self._id += 1
        request_id = self._id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self._read_line()
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpError("MCP error: {}".format(message["error"]))
            return message.get("result")

    def initialize(self) -> Dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "daythread", "version": __version__},
            },
        )
        self.notify("notifications/initialized")
        return result or {}

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise McpError("MCP client is not running")
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n"
        )
        self.proc.stdin.flush()

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self.request("tools/list", {}) or {}
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return result or {}

    def _read_line(self) -> str:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            ready, _, _ = select.select([self.proc.stdout], [], [], self.timeout)
            if not ready:
                raise McpError("MCP request timed out after {:.1f}s".format(self.timeout))
        except (OSError, ValueError):
            pass
        line = self.proc.stdout.readline()
        if not line:
            raise McpError("MCP server closed the stream")
        return line


def add_server(
    conn,
    server_id: str,
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if not server_id or not all(char.isalnum() or char in "_.-" for char in server_id):
        raise ValidationError("server id must be alphanumeric with _.-")
    if not command:
        raise ValidationError("command is required")
    validate_command(conn, command)
    resolved_command = shutil.which(command) or command
    if Path(resolved_command).exists():
        resolved_command = str(Path(resolved_command).resolve())
    resolved_args = []
    for arg in args or []:
        text = str(arg)
        if not Path(text).is_absolute() and Path(text).exists():
            text = str(Path(text).resolve())
        resolved_args.append(text)
    conn.execute(
        """
        INSERT OR REPLACE INTO mcp_servers(id, command, args, env, status, created_at, last_error)
        VALUES(?, ?, ?, ?, 'configured', ?, NULL)
        """,
        (
            server_id,
            resolved_command,
            canonical(resolved_args),
            canonical(dict(env or {})),
            utc_now(),
        ),
    )
    conn.commit()
    obs.audit(conn, "mcp.server.added", actor="user", target=server_id)
    return get_server(conn, server_id)


def get_server(conn, server_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
    if row is None:
        raise KeyError("MCP server not found: {}".format(server_id))
    item = row_to_dict(row)
    item["args"] = json_load(item["args"])
    item["env"] = json_load(item["env"])
    return item


def list_servers(conn) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM mcp_servers ORDER BY id").fetchall()
    items = []
    for row in rows:
        item = row_to_dict(row)
        item["args"] = json_load(item["args"])
        item["env"] = json_load(item["env"])
        items.append(item)
    return items


def remove_server(conn, server_id: str) -> None:
    get_server(conn, server_id)
    conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
    conn.commit()
    obs.audit(conn, "mcp.server.removed", actor="user", target=server_id)


def server_tools(conn, server_id: str, cwd: Optional[Path] = None) -> List[Dict[str, Any]]:
    server = get_server(conn, server_id)
    client = _client(conn, server, cwd)
    with client:
        client.initialize()
        tools = client.list_tools()
    obs.incr(conn, "mcp.tools.listed", len(tools))
    return tools


def call(
    conn,
    server_id: str,
    tool: str,
    arguments: Dict[str, Any],
    cwd: Optional[Path] = None,
) -> Dict[str, Any]:
    server = get_server(conn, server_id)
    client = _client(conn, server, cwd)
    with client:
        client.initialize()
        result = client.call_tool(tool, arguments)
    obs.incr(conn, "mcp.calls")
    obs.audit(conn, "mcp.called", actor="agent", target=server_id, detail={"tool": tool})
    return result


def validate_command(conn, command: str) -> None:
    allowed = get_setting(conn, "mcp.allowed_commands", DEFAULT_ALLOWED_COMMANDS) or DEFAULT_ALLOWED_COMMANDS
    names = {item.strip() for item in allowed.split(",") if item.strip()}
    name = Path(command).name
    if name not in names and not re.match(r"^python\d+(\.\d+)?$", name):
        raise ValidationError(
            "MCP command {} is not allowed (allowed: {})".format(name, ", ".join(sorted(names)))
        )


def _base_env() -> Dict[str, str]:
    return {key: os.environ[key] for key in BASE_ENV_KEYS if key in os.environ}


def _client(conn, server: Dict[str, Any], cwd: Optional[Path]) -> McpStdioClient:
    timeout = float(get_setting(conn, "mcp.timeout_seconds", str(DEFAULT_TIMEOUT)) or DEFAULT_TIMEOUT)
    validate_command(conn, str(server["command"]))
    env = _resolve_env(conn, server.get("env") or {}, cwd)
    command = [str(server["command"])] + [str(item) for item in server.get("args", [])]
    return McpStdioClient(command, env=env, cwd=cwd, timeout=timeout)


def _resolve_env(
    conn,
    env: Dict[str, str],
    cwd: Optional[Path],
) -> Dict[str, str]:
    resolved: Dict[str, str] = {}
    for key, value in env.items():
        if isinstance(value, str) and value.startswith("env:"):
            variable = value[len("env:"):]
            if variable not in os.environ:
                raise McpError(
                    "environment variable {} is not set for MCP server".format(variable)
                )
            resolved[str(key)] = os.environ[variable]
        elif isinstance(value, str) and value.startswith("secret:"):
            from .crypto import SecretVault

            home = Path(cwd) if cwd else Path.home()
            resolved[str(key)] = SecretVault(conn, home).get(value[len("secret:"):])
        else:
            resolved[str(key)] = str(value)
    return resolved
