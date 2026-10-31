"""Load webclaw/api/.env into os.environ.

Does not override variables already set in the process environment.
Expands ``~`` in values so paths like ``~/pms/data/webclaw.sqlite`` work.

Path resolution is cross-platform (``pathlib``): relative values are anchored
to the PMS workspace root (``pms/``). Missing ``ERPCLAW_HOME``,
``ERPCLAW_ENGINE_PATH``, ``DATA_DIR``, and DB path vars fall back to
``data/`` and ``erpclaw-engine/`` under that root.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

_LOADED = False


def _detect_pms_root() -> Path:
    """Locate the workspace root containing webclaw/ and erpclaw-engine/.

    Prefers the path relative to this file; if the layout is incomplete
    (e.g. relocated package), walks from ``Path.cwd()`` upward.
    """
    file_root = Path(__file__).resolve().parent.parent.parent
    markers = ("webclaw", "erpclaw-engine")

    def _is_pms(root: Path) -> bool:
        return all((root / name).is_dir() for name in markers)

    if _is_pms(file_root):
        return file_root

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _is_pms(candidate):
            return candidate
    return file_root


_API_DIR = Path(__file__).resolve().parent
_PMS_ROOT = _detect_pms_root()


def pms_root() -> str:
    """Workspace root containing webclaw/, erpclaw-engine/, hospitalityclaw/."""
    return str(_PMS_ROOT)


def _is_foreign_posix_abs(raw: str) -> bool:
    """Detect a Linux/macOS absolute path that cannot exist on native Windows."""
    if os.name != "nt":
        return False
    # Drive-letter or UNC paths are native; bare ``/home/...`` is not.
    if len(raw) >= 2 and raw[1] == ":":
        return False
    if raw.startswith("\\\\") or raw.startswith("//"):
        return False
    return raw.startswith("/")


def resolve_workspace_path(raw: str | None, *fallback_parts: str) -> str:
    """Resolve a path from .env against the PMS root when it is relative.

    On Windows, leftover POSIX absolute paths (e.g. ``/home/.../data``) are
    treated as missing so the PMS-root fallback is used instead.
    """
    if raw:
        expanded = os.path.expanduser(raw.strip())
        if _is_foreign_posix_abs(expanded):
            raw = None
        else:
            path = Path(expanded)
            if path.is_absolute():
                return str(path.resolve())
            return str((_PMS_ROOT / path).resolve())
    return str((_PMS_ROOT.joinpath(*fallback_parts)).resolve())


def load_api_env() -> None:
    global _LOADED
    if _LOADED:
        return
    _read_env_file(_API_DIR / ".env")
    _read_env_file(_PMS_ROOT / "erpclaw-engine" / ".env")
    _LOADED = True
    # Canonicalize paths for child processes (skills, MCP). Relative .env
    # values are expanded to absolute paths under the PMS root so subprocesses
    # do not depend on the caller's cwd.
    engine = resolve_workspace_path(os.environ.get("ERPCLAW_ENGINE_PATH"), "erpclaw-engine")
    os.environ["ERPCLAW_ENGINE_PATH"] = engine
    data = resolve_workspace_path(
        os.environ.get("DATA_DIR") or os.environ.get("ERPCLAW_HOME"),
        "data",
    )
    os.environ["ERPCLAW_HOME"] = data
    os.environ["DATA_DIR"] = data
    if "WEBCLAW_DB_PATH" in os.environ:
        os.environ["WEBCLAW_DB_PATH"] = resolve_workspace_path(os.environ.get("WEBCLAW_DB_PATH"))
    if "ERPCLAW_DB_PATH" in os.environ:
        os.environ["ERPCLAW_DB_PATH"] = resolve_workspace_path(os.environ.get("ERPCLAW_DB_PATH"))
    publish_db_env()


def _read_env_file(path: Path | str) -> None:
    path = Path(path)
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = os.path.expanduser(value)


def resolve_data_dir() -> str:
    """Shared data directory (``DATA_DIR`` or ``ERPCLAW_HOME``, else ``pms/data``)."""
    load_api_env()
    raw = os.environ.get("DATA_DIR") or os.environ.get("ERPCLAW_HOME")
    return resolve_workspace_path(raw, "data")


def normalize_dialect(raw: str | None) -> str:
    """Map ``DB_TYPE`` / dialect aliases onto ``postgresql`` (Postgres-only)."""
    value = (raw or "").strip().lower()
    if value in ("postgres", "postgresql", "pg", ""):
        return "postgresql"
    return value


def resolve_db_type() -> str:
    """Active backend. PMS is PostgreSQL-only; unset ``DB_TYPE`` means postgres."""
    load_api_env()
    explicit = (os.environ.get("DB_TYPE") or "").strip()
    if explicit:
        return normalize_dialect(explicit)
    legacy = os.environ.get("WEBCLAW_DB_DIALECT") or os.environ.get("ERPCLAW_DB_DIALECT")
    return normalize_dialect(legacy)


def require_platform_postgres() -> None:
    """Fail fast unless the WebClaw platform database is PostgreSQL.

    Auth, sessions, plans, billing, audit, and tickets must use Postgres via
    ``db_access``. SQLite is not a supported runtime.
    """
    load_api_env()
    kind = resolve_db_type()
    if kind != "postgresql":
        raw = (os.environ.get("DB_TYPE") or "").strip() or "unset"
        raise RuntimeError(
            "WebClaw requires DB_TYPE=postgres "
            f"(got {raw}). Set DB_HOST, DB_PORT, DB_NAME, DB_USER, and "
            "DB_PASSWORD (or WEBCLAW_DB_URL)."
        )
    url = postgres_url_from_env()
    if not url:
        for key in ("WEBCLAW_DB_URL", "ERPCLAW_DB_URL"):
            if _is_postgres_url(os.environ.get(key)):
                return
        raise RuntimeError(
            "DB_TYPE=postgres requires DB_HOST, DB_PORT, DB_NAME, DB_USER, "
            "and DB_PASSWORD (or WEBCLAW_DB_URL)."
        )


def _is_postgres_url(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    return lowered.startswith("postgres://") or lowered.startswith("postgresql://")


def postgres_url_from_env() -> str | None:
    """Build a libpq URL from ``DB_HOST``, ``DB_PORT``, ``DB_NAME``, ``DB_USER``, ``DB_PASSWORD``.

    Returns None when none of those variables are set, so a PostgreSQL
    dialect with only ``ERPCLAW_DB_URL`` keeps the previous error path.
    Host and port default to ``localhost`` and ``5432`` once any credential is present.
    """
    keys = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if not any((os.environ.get(key) or "").strip() for key in keys):
        return None
    host = (os.environ.get("DB_HOST") or "localhost").strip() or "localhost"
    port = (os.environ.get("DB_PORT") or "5432").strip() or "5432"
    name = (os.environ.get("DB_NAME") or "pms").strip() or "pms"
    user = (os.environ.get("DB_USER") or "postgres").strip() or "postgres"
    password = os.environ.get("DB_PASSWORD") or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    auth = quote(user, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"
    return f"postgresql://{auth}@{host}:{port}/{quote(name, safe='')}"


def publish_db_env() -> None:
    """Mirror ``DB_TYPE`` onto the dialect variables the rest of the stack already reads.

    When PostgreSQL is selected, fill ``ERPCLAW_DB_URL`` / ``WEBCLAW_DB_URL``
    from the discrete ``DB_*`` settings unless a postgres URL is already set.
    """
    explicit = (os.environ.get("DB_TYPE") or "").strip()
    if not explicit:
        return
    kind = normalize_dialect(explicit)
    os.environ["ERPCLAW_DB_DIALECT"] = kind
    os.environ["WEBCLAW_DB_DIALECT"] = kind
    if kind != "postgresql":
        return
    url = postgres_url_from_env()
    if not url:
        return
    for key in ("ERPCLAW_DB_URL", "WEBCLAW_DB_URL"):
        if not _is_postgres_url(os.environ.get(key)):
            os.environ[key] = url


def resolve_webclaw_db_path() -> str:
    """Legacy SQLite path for migrate/verify tooling only (not runtime)."""
    load_api_env()
    return resolve_workspace_path(os.environ.get("WEBCLAW_DB_PATH"), "data", "webclaw.sqlite")


def resolve_erpclaw_db_path() -> str:
    """Legacy ERP SQLite path for migrate/verify tooling only (not runtime)."""
    load_api_env()
    raw = os.environ.get("ERPCLAW_DB_PATH")
    if raw:
        return resolve_workspace_path(raw)
    return str(Path(resolve_data_dir()) / "data.sqlite")


def resolve_erpclaw_engine_path() -> str:
    """Local erpclaw-engine directory (ERPCLAW_ENGINE_PATH, else pms/erpclaw-engine)."""
    load_api_env()
    return resolve_workspace_path(os.environ.get("ERPCLAW_ENGINE_PATH"), "erpclaw-engine")


def resolve_erpclaw_lib_dir() -> str | None:
    """Directory containing ``erpclaw_lib`` (engine tree, not the data dir)."""
    load_api_env()
    candidates = [
        Path(resolve_erpclaw_engine_path()) / "scripts" / "erpclaw-setup" / "lib",
        Path(resolve_data_dir()) / "lib",
    ]
    for path in candidates:
        if (path / "erpclaw_lib").is_dir():
            return str(path)
    return None
