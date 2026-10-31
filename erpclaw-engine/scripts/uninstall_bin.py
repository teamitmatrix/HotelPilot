#!/usr/bin/env python3
"""uninstall_bin.py: remove the `erpclaw` shell shim from PATH.

Idempotent counterpart to install_bin.py. Removes symlinks / ``erpclaw.cmd``
launchers that point at our own shim; refuses to touch unrelated files.

Usage: python uninstall_bin.py [--home PATH] [--verbose]
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path


DEFAULT_HOME = Path(os.path.expanduser("~/.openclaw/erpclaw"))
SHIM_REL_PATH = "skills/erpclaw/bin/erpclaw"


def target_candidates() -> list[Path]:
    """Same priority order as install_bin.py. Scan all for removal."""
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        if platform.machine() == "arm64":
            return [
                Path("/opt/homebrew/bin"),
                home / ".local" / "bin",
                home / "bin",
            ]
        return [
            Path("/usr/local/bin"),
            home / ".local" / "bin",
            home / "bin",
        ]
    if system == "Linux":
        return [
            home / ".local" / "bin",
            home / "bin",
            Path("/usr/local/bin"),
        ]
    if system == "Windows":
        local_app = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        scripts = Path(sys.executable).resolve().parent
        return [
            local_app / "Programs" / "erpclaw",
            scripts,
            home / ".local" / "bin",
        ]
    return [home / ".local" / "bin", home / "bin"]


def uninstall(home: Path, verbose: bool = False) -> int:
    source = (home / SHIM_REL_PATH).resolve() if (home / SHIM_REL_PATH).exists() else home / SHIM_REL_PATH
    removed_any = False
    names = ("erpclaw", "erpclaw.cmd") if platform.system() == "Windows" else ("erpclaw",)
    for candidate in target_candidates():
        for name in names:
            target = candidate / name
            if not target.exists() and not target.is_symlink():
                continue
            if target.suffix.lower() == ".cmd":
                try:
                    text = target.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    print(f"error reading {target}: {exc}", file=sys.stderr)
                    return 1
                if str(source) not in text and (not source.exists() or str(source.resolve()) not in text):
                    if verbose:
                        print(f"skipping {target}: cmd shim does not reference our source", file=sys.stderr)
                    continue
                try:
                    target.unlink()
                    print(f"removed: {target}")
                    removed_any = True
                except OSError as exc:
                    print(f"error removing {target}: {exc}", file=sys.stderr)
                    return 1
                continue
            if not target.is_symlink():
                if verbose:
                    print(
                        f"skipping {target}: not a symlink (manual install?). "
                        f"Remove manually if desired.",
                        file=sys.stderr,
                    )
                continue
            existing = Path(os.readlink(target))
            if not existing.is_absolute():
                existing = target.parent / existing
            if existing.resolve() != source.resolve() if source.exists() else False:
                if verbose:
                    print(
                        f"skipping {target}: symlink points at {existing}, "
                        f"not our shim at {source}",
                        file=sys.stderr,
                    )
                continue
            try:
                target.unlink()
                print(f"removed: {target}")
                removed_any = True
            except OSError as exc:
                print(f"error removing {target}: {exc}", file=sys.stderr)
                return 1

    if not removed_any:
        print("no erpclaw shims found; nothing to uninstall.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home",
        default=os.environ.get("CLAWHUB_HOME", str(DEFAULT_HOME)),
        help=f"ERPClaw install root (default: {DEFAULT_HOME})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print detailed progress",
    )
    args = parser.parse_args()
    return uninstall(Path(args.home), verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
