"""Skill routes — schema discovery and action execution."""
import os
import re

import yaml
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

# Skill names: lowercase alphanumeric + hyphens only (path traversal defense)
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

from .executor import (
    execute_skill,
    find_skill_dir,
    find_skill_md,
    iter_skills,
)
from .skillmd_parser import get_cached_params, get_skill_actions
from .action_prober import probe_action_params
from .schema_introspector import introspect_child_tables
from .ui_generator import generate_ui_config
from events import emit_data_change
from adaptive.usage_tracker import track_action
from models import sanitize_skill_params
import db
from tenant import (
    TenantIsolationError,
    TenantContext,
    enforce_payload_tenant,
    payload_tenant_id,
    require_tenant_for_action,
)
from auth.deps import get_optional_tenant

# Cache: skill_name → company_id (avoids repeated DB queries)
_default_company_cache: dict[str, str | None] = {}


def _bind_tenant(skill: str, action: str, params: dict, ctx: TenantContext | None) -> dict:
    """Enforce tenant isolation on skill params before subprocess execution."""
    if ctx is None:
        # Unauthenticated skill access is only allowed during first-run (no users).
        return params
    require_tenant_for_action(ctx, skill, action)
    if skill.startswith("erpclaw") or skill == "hospitalityclaw":
        # Stash action name so enforce_payload_tenant can block writes under ALL.
        stamped = {**params, "_action": action}
        bound = enforce_payload_tenant(ctx, stamped)
        bound.pop("_action", None)
        return bound
    return params


def _execute_tenant(
    ctx: TenantContext | None, params: dict
) -> tuple[str | None, bool, str | None, list[str] | None]:
    """Return (tenant_id, is_superadmin, group_id, property_ids) for the subprocess."""
    if ctx is None:
        return None, False, None, None
    if ctx.scope_all_properties:
        props = list(ctx.assigned_properties) if ctx.assigned_properties else None
        return None, ctx.is_superadmin, ctx.group_id, props
    return (
        ctx.isolation_id or payload_tenant_id(params),
        ctx.is_superadmin,
        ctx.group_id,
        None,
    )

router = APIRouter(tags=["skills"])


def _validate_skill_name(skill: str) -> JSONResponse | None:
    """Return a 400 error response if skill name is invalid, else None."""
    if not _SKILL_NAME_RE.match(skill):
        return JSONResponse(
            {"status": "error", "message": "Invalid skill name"}, status_code=400
        )
    return None


@router.get("/api/v1/schema/actions/{skill}")
async def list_actions(skill: str):
    """Discover available actions for a skill.

    Three-level fallback:
    1. Subprocess probe: argparse choices= error or 'available' key in response
    2. Subprocess probe: parse action names from error/suggestion text
    3. YAML frontmatter: read action names from SKILL.md scripts[].actions[]
    """
    if err := _validate_skill_name(skill):
        return err
    result = await execute_skill(skill, "__discover__", {})
    actions = result.get("available_actions") or result.get("available")
    if actions:
        return {"status": "ok", "skill": skill, "actions": actions}

    # Level 2: Parse action names from suggestion/message/error text
    suggestion = result.get("suggestion", "") or result.get("message", "") or result.get("error", "")
    if "available:" in suggestion.lower():
        # Extract "Available: add-foo, list-foos, ..." or "Available actions: ..."
        idx = suggestion.lower().index("available:")
        raw = suggestion[idx + len("available:"):].strip()
        # Strip leading "actions:" if present (handles both "Available:" and "Available actions:")
        if raw.lower().startswith("actions:"):
            raw = raw[len("actions:"):].strip()
        parsed = [a.strip() for a in raw.split(",") if a.strip()]
        if parsed:
            return {"status": "ok", "skill": skill, "actions": parsed}

    # Level 3: Read from SKILL.md YAML frontmatter
    skill_md = find_skill_md(skill)
    yaml_actions = get_skill_actions(skill_md) if skill_md else []
    if yaml_actions:
        return {"status": "ok", "skill": skill, "actions": yaml_actions}

    return {"status": "error", "message": f"Could not discover actions for {skill}"}


@router.get("/api/v1/schema/params/{skill}")
async def get_skill_params(skill: str):
    """Return action parameter metadata parsed from SKILL.md body.

    Used by frontend to auto-generate forms without UI.yaml.
    Three-level fallback:
      1. SKILL.md table parsing (standard format)
      2. SKILL.md code block example parsing (e.g. payroll-style)
      3. Deep probe: run each action without args, parse error for required params
    """
    if err := _validate_skill_name(skill):
        return err
    skill_md = find_skill_md(skill)
    parsed = get_cached_params(skill, skill_md) if skill_md else None

    # Level 1+2: SKILL.md parsing (tables then code blocks) succeeded
    if parsed and parsed.get("actions"):
        return JSONResponse(
            {"status": "ok", "skill": skill, "schema_source": "skill.md", **parsed},
            headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=600"},
        )

    # Level 3: Deep probe — discover params by running actions without args
    # First, discover what actions exist
    discover_result = await execute_skill(skill, "__discover__", {})
    actions = discover_result.get("available_actions") or discover_result.get("available")
    if actions:
        probed = await probe_action_params(skill, actions)
        if probed and probed.get("actions"):
            return JSONResponse(
                {"status": "ok", "skill": skill, "schema_source": "probe", **probed},
                headers={"Cache-Control": "public, max-age=60"},
            )

    # No SKILL.md and no discoverable actions
    if not parsed:
        return JSONResponse(
            {"status": "error", "message": f"No SKILL.md found for {skill}"},
            status_code=404,
        )

    # SKILL.md exists but has no parseable action metadata
    return JSONResponse(
        {"status": "ok", "skill": skill, "schema_source": "skill.md", **parsed},
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/api/v1/schema/child-tables/{skill}")
async def get_child_tables(skill: str):
    """Return child table schemas via dialect-aware catalog introspection.

    Used by the frontend to auto-render repeatable row sections for child
    tables (items, details, etc.) without requiring manual UI.yaml config.
    """
    if err := _validate_skill_name(skill):
        return err
    child_tables = introspect_child_tables(skill)
    if not child_tables:
        return JSONResponse(
            {"status": "ok", "skill": skill, "child_tables": {}},
            headers={"Cache-Control": "public, max-age=600"},
        )
    return JSONResponse(
        {"status": "ok", "skill": skill, "child_tables": child_tables},
        headers={"Cache-Control": "public, max-age=600"},
    )


@router.get("/api/v1/schema/ui-config/{skill}")
async def get_ui_config(skill: str):
    """Return UIConfig for a skill.

    Priority:
      1. Hand-written UI.yaml (if exists) — parsed and returned as JSON
      2. Auto-generated from SKILL.md metadata (cached to disk)
    """
    if err := _validate_skill_name(skill):
        return err
    skill_dir = find_skill_dir(skill)
    if skill_dir:
        ui_path = os.path.join(skill_dir, "UI.yaml")
        if os.path.isfile(ui_path):
            try:
                with open(ui_path) as f:
                    config = yaml.safe_load(f.read())
                return JSONResponse(
                    config,
                    headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=300"},
                )
            except Exception:
                return JSONResponse(
                    {"error": "Failed to parse UI.yaml"}, status_code=500
                )

    # Auto-generate from SKILL.md
    config = generate_ui_config(skill)
    if not config:
        return JSONResponse(
            {"error": f"No UI.yaml or SKILL.md found for '{skill}'"},
            status_code=404,
        )

    return JSONResponse(
        config,
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=600"},
    )


@router.get("/api/v1/schema/skills")
async def list_skills():
    """List installed skills with metadata from SKILL.md frontmatter.

    Discovers skills under the PMS project (hospitalityclaw, webclaw) and
    maps ``erpclaw`` to ERPCLAW_ENGINE_PATH (erpclaw-engine).
    """
    skills = []
    for skill_name, skill_md_path in iter_skills():
        skill_dir = os.path.dirname(skill_md_path)
        meta = {"name": skill_name}

        # All skills with a SKILL.md are web-renderable (UI auto-generates if no UI.yaml)
        meta["has_ui"] = True
        meta["has_ui_yaml"] = os.path.isfile(os.path.join(skill_dir, "UI.yaml"))

        try:
            with open(skill_md_path, "r") as f:
                content = f.read()
            if content.startswith("---"):
                end = content.index("---", 3)
                frontmatter = yaml.safe_load(content[3:end])
                if frontmatter:
                    meta["description"] = frontmatter.get("description", "")
                    meta["version"] = frontmatter.get("version", "")
                    meta["category"] = frontmatter.get("category", "")
                    meta["tier"] = frontmatter.get("tier", 0)
                    meta["tags"] = frontmatter.get("tags", [])
                    meta["requires"] = frontmatter.get("requires", [])
                    # Read webclaw config if present (for smart UI)
                    webclaw = frontmatter.get("webclaw")
                    if webclaw and isinstance(webclaw, dict):
                        meta["webclaw"] = webclaw
                        # webclaw.category overrides top-level category
                        if webclaw.get("category"):
                            meta["category"] = webclaw["category"]
                    # Auto-detect category from skill name prefix if still empty
                    if not meta["category"] and "-" in skill_name:
                        prefix = skill_name.split("-")[0]
                        # Map known prefixes to categories
                        PREFIX_CATEGORIES = {
                            "auditclaw": "compliance",
                        }
                        meta["category"] = PREFIX_CATEGORIES.get(prefix, "")
        except Exception:
            pass

        skills.append(meta)

    return {"status": "ok", "skills": skills, "count": len(skills)}


@router.get("/api/v1/schema/action-index")
async def action_index():
    """Return a flat index of all actions across all installed skills.

    Used by the Cmd+K command palette for cross-skill action search.
    Each entry has: action name, skill name, action type, entity group.
    Cached for 5 minutes. Typically ~600 actions across 25 skills.
    """
    actions = []
    for skill_name, skill_md_path in iter_skills():

        parsed = get_cached_params(skill_name, skill_md_path)
        if not parsed or not parsed.get("actions"):
            continue

        # Build entity group lookup
        entity_groups: dict[str, str] = {}
        for group in parsed.get("entity_groups", []):
            group_name = group.get("name", "")
            for a in group.get("actions", []):
                entity_groups[a] = group_name

        for action_name in parsed["actions"]:
            # Derive action type from name prefix
            atype = "other"
            for prefix in ("list-", "add-", "create-", "get-", "update-",
                           "submit-", "cancel-", "delete-"):
                if action_name.startswith(prefix):
                    atype = prefix.rstrip("-")
                    break

            actions.append({
                "action": action_name,
                "skill": skill_name,
                "type": atype,
                "group": entity_groups.get(action_name, ""),
            })

    return JSONResponse(
        {"status": "ok", "actions": actions, "count": len(actions)},
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=600"},
    )


@router.get("/api/v1/activity")
async def get_activity(limit: int = 10, _tenant=Depends(get_optional_tenant)):
    """Recent activity from audit_log table in the erpclaw database."""
    try:
        conn = db.get_skill_db("erpclaw")
        if conn is None:
            return {"status": "ok", "activity": []}
        rows = conn.execute(
            "SELECT skill, action, user_id, timestamp as created_at "
            "FROM audit_log ORDER BY timestamp DESC LIMIT ?",
            (min(limit, 50),),
        ).fetchall()
        activity = []
        for r in rows:
            if hasattr(r, "keys"):
                activity.append(dict(r))
            else:
                activity.append(
                    {
                        "skill": r[0],
                        "action": r[1],
                        "user_id": r[2],
                        "created_at": r[3],
                    }
                )
        conn.close()
        return {"status": "ok", "activity": activity}
    except Exception:
        return {"status": "ok", "activity": []}


def _is_success(result: dict) -> bool:
    """Determine if a skill action result is successful.

    Supports both ERPClaw-style (status: "ok") and standalone skills
    that return data without a status key.
    """
    if result.get("status") in ("ok", "partial"):
        return True
    if "error" in result or result.get("status") == "error":
        return False
    # No status key and no error key — treat as success (standalone skill response)
    return "status" not in result


@router.get("/api/v1/{skill}/{action}")
async def get_action(
    skill: str,
    action: str,
    request: Request,
    tenant: TenantContext | None = Depends(get_optional_tenant),
):
    """Handle GET requests (list-*, get-*, status, etc.)."""
    params = sanitize_skill_params(dict(request.query_params))
    try:
        params = _bind_tenant(skill, action, params, tenant)
    except TenantIsolationError as exc:
        return JSONResponse(
            {"status": "error", "message": exc.message},
            status_code=exc.status_code,
        )
    tenant_id, is_superadmin, group_id, property_ids = _execute_tenant(tenant, params)
    result = await execute_skill(
        skill,
        action,
        params,
        tenant_id=tenant_id,
        is_superadmin=is_superadmin,
        group_id=group_id,
        property_ids=property_ids,
    )
    status_code = 200 if _is_success(result) else 400
    return JSONResponse(result, status_code=status_code)


@router.post("/api/v1/{skill}/{action}")
async def post_action(
    skill: str,
    action: str,
    request: Request,
    tenant: TenantContext | None = Depends(get_optional_tenant),
):
    """Handle POST requests (add-*, update-*, submit-*, cancel-*, delete-*)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    params = dict(request.query_params)
    params.update(body)
    params = sanitize_skill_params(params)
    try:
        params = _bind_tenant(skill, action, params, tenant)
    except TenantIsolationError as exc:
        return JSONResponse(
            {"status": "error", "message": exc.message},
            status_code=exc.status_code,
        )
    tenant_id, is_superadmin, group_id, property_ids = _execute_tenant(tenant, params)
    if tenant_id and action in (
        "add-room",
        "hospitality-add-room",
        "create-room",
        "hospitality-create-room",
    ):
        try:
            from admin_growth import assert_within_plan_limits

            limit_err = assert_within_plan_limits(str(tenant_id), "rooms", delta=1)
            if limit_err:
                return JSONResponse(limit_err, status_code=403)
        except Exception:
            pass
    result = await execute_skill(
        skill,
        action,
        params,
        tenant_id=tenant_id,
        is_superadmin=is_superadmin,
        group_id=group_id,
        property_ids=property_ids,
    )
    success = _is_success(result)
    status_code = 200 if success else 400

    # Emit data-change event for successful mutating actions (SSE subscribers)
    if success and not action.startswith(("list-", "get-")):
        entity = action.split("-", 1)[1] if "-" in action else action
        await emit_data_change(skill, entity)

        # Track usage for expansion prompts (non-blocking, never fails the action)
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            track_action(user_id, skill, action)

        # Guest notifications (booking confirmation, folio receipt) — never fail the action
        try:
            from notifications.engine import dispatch_for_skill_action
            import asyncio

            asyncio.create_task(
                dispatch_for_skill_action(
                    skill=skill,
                    action=action,
                    tenant_id=tenant_id,
                    result=result if isinstance(result, dict) else {},
                    params=params,
                )
            )
        except Exception:
            pass

        # Push updated availability and rates to connected OTAs. Never fail the action.
        try:
            from services.channel_manager import sync_after_skill_action
            import asyncio

            asyncio.create_task(
                sync_after_skill_action(
                    skill=skill,
                    action=action,
                    tenant_id=tenant_id,
                    result=result if isinstance(result, dict) else {},
                    params=params,
                )
            )
        except Exception:
            pass

    return JSONResponse(result, status_code=status_code)
