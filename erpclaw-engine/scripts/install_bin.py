#!/usr/bin/env python3
"""install_bin.py: put the `erpclaw` shell shim on the user's PATH.

Runs idempotently. Called by `clawhub install erpclaw` as a post-install hook,
or manually by a user who wants to put `erpclaw` back on PATH.

Strategy:
    1. Source:  <CLAWHUB_HOME>/skills/erpclaw/bin/erpclaw
    2. Target:  the first writable directory from a platform-specific candidate list
       macOS Apple Silicon:    /opt/homebrew/bin
       macOS Intel:            /usr/local/bin
       Linux:                  ~/.local/bin
       Linux fallback:         ~/bin
       Windows:                %LOCALAPPDATA%\\Programs\\erpclaw, then Scripts dir
    3. Create a symlink (Unix) or ``erpclaw.cmd`` shim (Windows).
    4. Warn if the target directory is not on PATH and print the export line.

Exits 0 on success (or idempotent no-op), non-zero on failure.

Usage: python install_bin.py [--home PATH] [--verbose]
"""
from __future__ import annotations

import argparse
import os
import platform
import stat
import sys
from pathlib import Path


DEFAULT_HOME = Path(os.path.expanduser("~/.openclaw/erpclaw"))
SHIM_REL_PATH = "skills/erpclaw/bin/erpclaw"


def target_candidates() -> list[Path]:
    """Return PATH-install candidates in priority order for this platform."""
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        mac_arch = platform.machine()
        if mac_arch == "arm64":
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
    # Unknown platform: best-effort XDG fallback
    return [home / ".local" / "bin", home / "bin"]


def is_writable_dir(path: Path) -> bool:
    if not path.exists():
        return False
    if not path.is_dir():
        return False
    return os.access(path, os.W_OK)


def already_on_path(directory: Path) -> bool:
    paths = os.environ.get("PATH", "").split(os.pathsep)
    resolved = {os.path.normcase(os.path.abspath(p)) for p in paths if p}
    return os.path.normcase(str(directory.resolve())) in resolved


def _write_windows_cmd(target: Path, source: Path) -> None:
    """Write a ``.cmd`` launcher that invokes the shim with the current Python."""
    # Escape for cmd.exe: wrap paths in quotes.
    py = str(Path(sys.executable).resolve())
    src = str(source.resolve())
    content = (
        "@echo off\r\n"
        f'"{py}" "{src}" %*\r\n'
    )
    target.write_text(content, encoding="utf-8", newline="")


def install(home: Path, verbose: bool = False) -> int:
    source = home / SHIM_REL_PATH
    if not source.exists():
        print(
            f"error: shim not found at {source}. "
            f"Is ERPClaw installed at {home}?",
            file=sys.stderr,
        )
        return 2

    # Ensure source is executable (no-op on Windows NTFS for mode bits)
    try:
        st = source.stat()
        source.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        print(f"warning: could not chmod {source}: {exc}", file=sys.stderr)

    candidates = target_candidates()
    target_dir = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if is_writable_dir(candidate):
            target_dir = candidate
            break

    if target_dir is None:
        fallback = Path.home() / ".local" / "bin"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if is_writable_dir(fallback):
            target_dir = fallback
        else:
            print(
                "error: could not find a writable directory in PATH "
                "candidates and could not create a fallback install dir",
                file=sys.stderr,
            )
            print(f"  tried: {', '.join(str(c) for c in candidates)}", file=sys.stderr)
            return 3

    is_windows = platform.system() == "Windows"
    target = target_dir / ("erpclaw.cmd" if is_windows else "erpclaw")

    if is_windows:
        _write_windows_cmd(target, source)
        if verbose:
            print(f"installed: {target} -> {source}")
    elif target.is_symlink():
        existing = Path(os.readlink(target))
        if not existing.is_absolute():
            existing = target.parent / existing
        if existing.resolve() == source.resolve():
            if verbose:
                print(f"already installed: {target} -> {source}")
        else:
            target.unlink()
            try:
                target.symlink_to(source)
            except OSError:
                # Symlink denied — fall back to a small wrapper script.
                target.write_text(
                    f"#!/usr/bin/env bash\nexec \"{sys.executable}\" \"{source}\" \"$@\"\n",
                    encoding="utf-8",
                )
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if verbose:
                print(f"replaced stale symlink: {target} -> {source}")
    elif target.exists():
        print(
            f"error: {target} exists and is not a symlink. "
            f"Refusing to overwrite. Remove it manually and re-run.",
            file=sys.stderr,
        )
        return 4
    else:
        try:
            target.symlink_to(source)
        except OSError:
            target.write_text(
                f"#!/usr/bin/env bash\nexec \"{sys.executable}\" \"{source}\" \"$@\"\n",
                encoding="utf-8",
            )
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if verbose:
            print(f"installed: {target} -> {source}")

    print(f"erpclaw installed at {target}")

    if not already_on_path(target_dir):
        print()
        print(f"NOTE: {target_dir} is not on your PATH.")
        if is_windows:
            print("Add this directory to your user PATH (PowerShell):")
            print(
                f'  [Environment]::SetEnvironmentVariable("Path", '
                f'$env:Path + ";{target_dir}", "User")'
            )
            print("Then open a new terminal and try: erpclaw --version")
        else:
            print("Add this line to your shell profile (~/.zshrc, ~/.bashrc, etc):")
            print(f'  export PATH="{target_dir}:$PATH"')
            print("Then reload your shell (exec $SHELL) and try: erpclaw --version")
        return 5  # success but warning

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
    return install(Path(args.home), verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
