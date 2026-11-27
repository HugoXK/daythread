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
