"""Build a standalone `dt` sidecar for the Tauri bundle."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def target_triple() -> str:
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "darwin":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return "{}-apple-darwin".format(arch)
    if system == "linux":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return "{}-unknown-linux-gnu".format(arch)
    if system == "windows":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return "{}-pc-windows-msvc".format(arch)
    return "{}-unknown".format(machine)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default=str(ROOT / "desktop-tauri" / "src-tauri" / "binaries")
    )
    args = parser.parse_args(argv)
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is required: python -m pip install 'daythread[sidecar]'",
            file=sys.stderr,
        )
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    build = ROOT / "build" / "sidecar"
    name = "dt-{}".format(target_triple())
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(build),
        "--workpath",
        str(build / "work"),
        "--specpath",
        str(build),
        str(ROOT / "scripts" / "dt_entry.py"),
    ]
    subprocess.run(command, cwd=str(ROOT), check=True)
    produced = build / name
    target = out / name
    shutil.copy2(produced, target)
    target.chmod(0o755)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
