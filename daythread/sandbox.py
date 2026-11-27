"""Optional OS-level sandbox for isolated tool subprocesses."""

from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from typing import List, Optional

from .db import get_setting
from .errors import ValidationError


def backend(conn) -> str:
    return str(get_setting(conn, "sandbox.backend", "none") or "none")


def wrap(
    command: List[str],
    conn,
    home: Path,
    allow_network: bool = True,
) -> List[str]:
    selected = backend(conn)
    if selected in ("", "none", "off"):
        return command
    if selected == "sandbox-exec":
        return _sandbox_exec(command, Path(home), allow_network)
    if selected == "docker":
        return _docker(command, conn, Path(home), allow_network)
    if selected == "bwrap":
        return _bwrap(command, Path(home), allow_network)
    raise ValidationError("unknown sandbox backend: {}".format(selected))


def _sandbox_exec(
    command: List[str],
    home: Path,
    allow_network: bool,
) -> List[str]:
    executable = shutil.which("sandbox-exec")
    if platform.system() != "Darwin" or not executable:
        raise ValidationError("sandbox-exec is only available on macOS")
    home_path = json.dumps(str(Path(home).resolve()))
    lines = [
        "(version 1)",
        "(allow default)",
    ]
    if not allow_network:
        lines.append("(deny network*)")
    lines.extend(
        [
            "(deny file-write*)",
            "(allow file-write*",
            "  (subpath {})".format(home_path),
            '  (subpath "/tmp")',
            '  (subpath "/private/tmp")',
            '  (subpath "/private/var/folders")',
            '  (subpath "/var/folders")',
            '  (literal "/dev/null")',
            ")",
        ]
    )
    return [executable, "-p", "\n".join(lines)] + list(command)


def _docker(
    command: List[str],
    conn,
    home: Path,
    allow_network: bool,
) -> List[str]:
    executable = shutil.which("docker")
    if not executable:
        raise ValidationError("docker is not installed")
    home = Path(home).resolve()
    image = str(get_setting(conn, "sandbox.docker_image", "daythread:latest"))
    default_network = "bridge" if allow_network else "none"
    network = str(
        get_setting(conn, "sandbox.docker_network", default_network) or default_network
    )
    package_root = Path(__file__).resolve().parent.parent
    return [
        executable,
        "run",
        "--rm",
        "--network",
        network,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-v",
        "{}:{}".format(home, home),
        "-v",
        "{}:{}:ro".format(package_root, package_root),
        "-w",
        str(package_root),
        "-e",
        "DAYTHREAD_HOME={}".format(home),
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        image,
    ] + list(command)


def _bwrap(
    command: List[str],
    home: Path,
    allow_network: bool,
) -> List[str]:
    executable = shutil.which("bwrap")
    if not executable:
        raise ValidationError("bubblewrap (bwrap) is not installed")
    home = Path(home).resolve()
    wrapped = [
        executable,
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        str(home),
        str(home),
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
    ]
    if not allow_network:
        wrapped.append("--unshare-net")
    return wrapped + list(command)
