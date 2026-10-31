"""Skill subprocess execution — runs db_query.py scripts and returns JSON."""
import glob
import asyncio
import json
import os
import re
import subprocess
import sys

from env_loader import (
    load_api_env,
    pms_root,
    resolve_erpclaw_engine_path,
    resolve_erpclaw_lib_dir,
    resolve_workspace_path,
)

load_api_env()

# Skill names must be lowercase alphanumeric with hyphens only (path traversal defense)
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Folder names under the PMS root that are not invocable skills.
_SKIP_SKILL_DIR_NAMES = {"erpclaw-engine", "data"}


def skill_dir_aliases() -> dict[str, str]:
    """Canonical skill name → directory when the folder name differs.

    ``erpclaw`` lives at ERPCLAW_ENGINE_PATH (pms/erpclaw-engine), not at
    ~/.openclaw/skills/erpclaw.
    """
    engine = resolve_erpclaw_engine_path()
    if os.path.isdir(engine):
        return {"erpclaw": engine}
    return {}


def skill_search_dirs() -> list[str]:
    """Directories that contain ``{skill_name}/SKILL.md`` (PMS-local first)."""
    dirs: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        path = os.path.abspath(os.path.expanduser(path))
        if path not in seen:
            seen.add(path)
            dirs.append(path)

    extra = os.environ.get("WEBCLAW_SKILLS_DIR") or os.environ.get("SKILLS_DIR")
    if extra:
        _add(resolve_workspace_path(extra))
    _add(pms_root())
    engine = resolve_erpclaw_engine_path()
    _add(os.path.join(engine, "modules"))
    home = os.environ.get("ERPCLAW_HOME")
    if home:
        _add(os.path.join(resolve_workspace_path(home), "modules"))
    return dirs


# Primary skills root for callers that still glob SKILLS_DIR.
SKILLS_DIR = pms_root()
MODULES_DIR = os.path.join(resolve_erpclaw_engine_path(), "modules")


def find_skill_dir(skill: str) -> str | None:
    """Resolve the root directory for a skill (alias or ``{search_dir}/{skill}``)."""
    if not _SKILL_NAME_RE.match(skill) or skill in _SKIP_SKILL_DIR_NAMES:
        return None
    aliased = skill_dir_aliases().get(skill)
    if aliased and os.path.isdir(aliased):
        return aliased
    for base in skill_search_dirs():
        candidate = os.path.join(base, skill)
        if os.path.isdir(candidate):
            return candidate
    return None


def find_skill_md(skill: str) -> str | None:
    """Resolve SKILL.md for a skill."""
    skill_dir = find_skill_dir(skill)
    if not skill_dir:
        return None
    path = os.path.join(skill_dir, "SKILL.md")
    return path if os.path.isfile(path) else None


def iter_skills() -> list[tuple[str, str]]:
    """Installed skills as ``(canonical_name, SKILL.md path)``. First name wins."""
    seen: set[str] = set()
    skills: list[tuple[str, str]] = []

    for name, directory in skill_dir_aliases().items():
        md = os.path.join(directory, "SKILL.md")
        if os.path.isfile(md) and name not in seen:
            seen.add(name)
            skills.append((name, md))

    for base in skill_search_dirs():
        for p in sorted(glob.glob(os.path.join(base, "*/SKILL.md"))):
            skill_name = os.path.basename(os.path.dirname(p))
            if (
                skill_name in _SKIP_SKILL_DIR_NAMES
                or skill_name in seen
                or not _SKILL_NAME_RE.match(skill_name)
            ):
                continue
            seen.add(skill_name)
            skills.append((skill_name, p))
    return skills


def iter_skill_md_paths() -> list[str]:
    """All SKILL.md files under skill search dirs (first name wins)."""
    return [path for _, path in iter_skills()]


def find_skill_script(skill: str) -> str | None:
    """Resolve the db_query.py path for a skill."""
    skill_dir = find_skill_dir(skill)
    if not skill_dir:
        return None
    script = os.path.join(skill_dir, "scripts", "db_query.py")
    return script if os.path.exists(script) else None


# Isolation / foreign-key *-id params that coexist with a primary --id.
# These must NOT cause generic --id to be dropped (assign-room sends id + room_id).
_FK_OR_TENANT_ID_KEYS = frozenset({
    "company-id",
    "tenant-id",
    "hotel-id",
    "room-id",
    "room-type-id",
    "guest-id",
    "folio-id",
    "reservation-id",
    "rate-plan-id",
    "tax-template-id",
    "tax-rule-id",
    "user-id",
    "role-id",
    "parent-id",
    "account-id",
    "customer-id",
    "supplier-id",
    "item-id",
    "warehouse-id",
    "project-id",
    "cost-center-id",
})


# Scope keys belong in the subprocess env (ERPCLAW_GROUP_ID / ERPCLAW_TENANT_IDS),
# not in skill argparse — scripts reject unknown --group-id / --property-ids.
_INTERNAL_SCOPE_CLI_KEYS = frozenset({"group-id", "property-ids"})

# Router-only store_true flags (no value). The foundation gate checks
# ``"--user-confirmed" in sys.argv`` and strips the bare flag before domain
# argparse — a trailing ``1`` would be left behind and break parsing.
_FLAG_ONLY_KEYS = frozenset({"user-confirmed"})


def build_cli_args(action: str, params: dict) -> list[str]:
    """Convert action + params dict into CLI argument list.

    Boolean values are passed as "1"/"0" strings since erpclaw scripts use
    value-based boolean args (e.g. --exempt-from-sales-tax 1), not store_true.
    False booleans are omitted entirely (absence = false for all erpclaw scripts).
    Keys in ``_FLAG_ONLY_KEYS`` emit a bare ``--flag`` when truthy.

    When both a generic 'id' and a primary-entity alias (e.g. 'lease-id') are
    present, the generic 'id' is skipped so skills that only define the alias
    do not get an unexpected --id. Tenant keys and foreign keys such as
    room-id / hotel-id never trigger that skip — they are companions to --id.
    """
    norm_keys = {k.replace("_", "-") for k in params}
    has_primary_alias = any(
        k.endswith("-id") and k != "id" and k not in _FK_OR_TENANT_ID_KEYS
        for k in norm_keys
    )
    skip_generic_id = has_primary_alias and ("id" in norm_keys)

    args = ["--action", action]
    for key, value in params.items():
        if key.startswith("_"):
            continue
        norm_key = key.replace("_", "-")
        if norm_key in _INTERNAL_SCOPE_CLI_KEYS:
            continue
        if skip_generic_id and norm_key == "id":
            continue
        flag = f"--{norm_key}"
        if norm_key in _FLAG_ONLY_KEYS:
            if value is True or (isinstance(value, str) and value.lower() in ("1", "true", "yes")):
                args.append(flag)
            continue
        if isinstance(value, bool):
            if value:
                args.extend([flag, "1"])
        elif isinstance(value, str) and value.lower() in ("true", "false"):
            if value.lower() == "true":
                args.extend([flag, "1"])
        elif value is not None and str(value).strip():
            args.extend([flag, str(value)])
    return args


async def execute_skill(
    skill: str,
    action: str,
    params: dict,
    timeout: int | None = None,
    tenant_id: str | None = None,
    is_superadmin: bool = False,
    group_id: str | None = None,
    property_ids: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Execute a skill action via subprocess and return parsed JSON.

    ``tenant_id`` is injected into the child environment as ERPCLAW_TENANT_ID
    so every db_query.py call is row-isolated even if a flag is omitted.

    When aggregating a group (``X-Property-ID=ALL``), pass ``group_id`` and
    ``property_ids`` instead of a single ``tenant_id``; the SQL rewriter uses
    ``IN (...)`` / ``group_id =`` predicates.
    """
    script = find_skill_script(skill)
    if not script:
        return {"status": "error", "message": f"Skill not found: {skill}"}

    cmd = [sys.executable, script] + build_cli_args(action, params)

    # Build PYTHONPATH: include the skill's own scripts dir + any shared libs
    skill_root = find_skill_dir(skill) or ""
    python_paths = [os.path.join(skill_root, "scripts")] if skill_root else []
    erpclaw_lib = resolve_erpclaw_lib_dir()
    if erpclaw_lib:
        python_paths.append(erpclaw_lib)
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        python_paths.append(existing)

    child_env = {**os.environ, "PYTHONPATH": os.pathsep.join(python_paths)}
    child_env.pop("ERPCLAW_TENANT_ID", None)
    child_env.pop("ERPCLAW_TENANT_STRICT", None)
    child_env.pop("ERPCLAW_TENANT_ROLE", None)
    child_env.pop("ERPCLAW_GROUP_ID", None)
    child_env.pop("ERPCLAW_TENANT_IDS", None)
    if tenant_id:
        child_env["ERPCLAW_TENANT_ID"] = tenant_id
        child_env["ERPCLAW_TENANT_STRICT"] = "1"
        child_env["ERPCLAW_TENANT_ROLE"] = "superadmin" if is_superadmin else "hotel"
    elif property_ids:
        # Group-wide read aggregation (no single isolation UUID).
        child_env["ERPCLAW_TENANT_IDS"] = ",".join(str(p) for p in property_ids if p)
        child_env["ERPCLAW_TENANT_STRICT"] = "1"
        child_env["ERPCLAW_TENANT_ROLE"] = "superadmin" if is_superadmin else "hotel"
    elif is_superadmin:
        child_env["ERPCLAW_TENANT_ROLE"] = "superadmin"
    if group_id:
        child_env["ERPCLAW_GROUP_ID"] = group_id

    # Seed-demo-data can take 60-90s due to many subprocess calls
    if timeout is None:
        timeout = 180 if action in ("seed-demo-data", "setup-company", "hospitality-seed-defaults", "hospitality-initialize-schema") else 30

    # Use a worker thread + subprocess.run. asyncio.create_subprocess_exec is
    # NotImplementedError on Windows when uvicorn uses SelectorEventLoop
    # (common with --reload), which surfaced as empty "Subprocess error: ".
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            env=child_env,
            timeout=timeout,
        )
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"Action timed out ({timeout}s)"}
    except Exception as e:
        detail = str(e).strip() or type(e).__name__
        return {"status": "error", "message": f"Subprocess error: {detail}"}

    output = stdout.decode("utf-8").strip()
    if not output:
        err = stderr.decode("utf-8").strip()
        # Pattern 1: erpclaw argparse — choices constraint error
        if "error: argument --action: invalid choice:" in err:
            lines = err.split("\n")
            error_line = [l for l in lines if "error: argument --action:" in l]
            msg = error_line[0].split("error: ")[-1] if error_line else "Invalid action"
            choices_match = re.search(r"choose from (.+)\)", err)
            if choices_match:
                actions = [a.strip().strip("'") for a in choices_match.group(1).split(",")]
                return {"status": "error", "message": msg, "available_actions": actions}
            return {"status": "error", "message": msg}
        # Pattern 2: stderr may contain JSON (some skills write errors to stderr)
        # Look for a JSON object in stderr (skip SyntaxWarning lines etc.)
        for line in reversed(err.split("\n")):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        if len(err) > 500:
            err = err[:500] + "..."
        return {"status": "error", "message": err or "No output from skill"}

    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return {"status": "error", "message": f"Invalid JSON: {output[:200]}"}

    # Auto-enrich with _ui directives if skill didn't provide them
    if "_ui" not in result:
        result["_ui"] = _auto_ui(action, result)
    return result


def _auto_ui(action: str, result: dict) -> dict | None:
    """Generate basic _ui directives for skill responses that lack them."""
    ui: dict = {}
    status = result.get("status", "")
    msg = result.get("message", "")

    if status == "error":
        ui["toast"] = {"type": "error", "message": msg or "Action failed", "duration": 0}
    elif action.startswith("add-"):
        name = result.get("name") or result.get("id", "")
        ui["toast"] = {"type": "success", "message": f"Created {name}" if name else "Created successfully"}
    elif action.startswith("submit-"):
        ui["toast"] = {"type": "success", "message": msg or "Submitted successfully"}
    elif action.startswith("cancel-"):
        ui["toast"] = {"type": "warning", "message": msg or "Cancelled"}
    elif action.startswith("delete-"):
        ui["toast"] = {"type": "info", "message": msg or "Deleted"}
    elif action.startswith("update-"):
        ui["toast"] = {"type": "success", "message": msg or "Updated successfully"}

    return ui if ui else None
