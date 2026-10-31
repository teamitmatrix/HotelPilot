"""AI Assistant chat client with multi-LLM routing.

Builds the system prompt (session scope, skills, entities) and routes
completions through ``chat.llm`` — OpenAI, Anthropic, or Gemini —
with task-based selection and failover.
"""
from typing import AsyncGenerator

from .action_schemas import get_action_schema
from .llm import ChatRequest, get_router
from .tools.runner import tool_catalog_prompt


def _session_scope_block(context: dict) -> list[str]:
    """Route system prompt by Hotel vs Platform Superadmin session scope."""
    tenant = context.get("tenant") or {}
    view_mode = (
        context.get("viewMode")
        or context.get("view_mode")
        or tenant.get("view_mode")
        or ""
    )
    is_superadmin = bool(
        context.get("is_superadmin")
        if context.get("is_superadmin") is not None
        else tenant.get("is_superadmin")
    )
    # Prefer trusted middleware tenant over client-only flags
    if tenant.get("is_superadmin") and not tenant.get("impersonating"):
        is_superadmin = True
        view_mode = view_mode or "platform_superadmin"
    elif tenant.get("impersonating") or view_mode == "hotel_impersonation":
        is_superadmin = False
        view_mode = "hotel_impersonation"
    elif view_mode in ("hotel_ops", "hotel_impersonation") or not is_superadmin:
        if view_mode != "hotel_impersonation" and (
            tenant.get("tenant_id")
            or context.get("hotel_id")
            or context.get("current_branch")
        ):
            view_mode = view_mode or "hotel_ops"

    role = (
        context.get("user_role_label")
        or context.get("user_role")
        or (tenant.get("roles") or [None])[0]
        or "User"
    )
    hotel_id = context.get("hotel_id") or tenant.get("tenant_id")
    hotel_name = context.get("hotel_name") or context.get("current_branch_name")
    branch_id = (
        context.get("current_branch")
        or tenant.get("active_property_id")
        or hotel_id
    )
    branch_name = context.get("current_branch_name") or hotel_name
    schema_scope = (
        context.get("schema_scope")
        or tenant.get("schema_scope")
        or ("platform" if is_superadmin else "hotel")
    )
    portfolio = bool(
        context.get("scope_all_properties")
        or tenant.get("scope_all_properties")
        or schema_scope == "portfolio"
    )

    parts: list[str] = ["\n**Session scope (authoritative):**"]

    if is_superadmin and view_mode == "platform_superadmin":
        parts.extend(
            [
                "- Mode: **Platform Global Admin** (`is_superadmin=true`)",
                f"- User role: **{role}**",
                "- Schema scope: **platform** (no tenant isolation)",
                "- You MAY query cross-tenant performance, platform metrics,",
                "  tenant management, and global operational APIs.",
                "- Do not invent hotel-specific facts; when a property is required,",
                "  ask which tenant/property to inspect.",
            ]
        )
        return parts

    # Hotel Owner / staff / impersonation — strict branch isolation
    label = hotel_name or branch_name or "assigned hotel"
    parts.extend(
        [
            f"- Mode: **Hotel View** ({label})",
            f"- User role: **{role}**",
            f"- Schema scope: **{schema_scope}**",
        ]
    )
    if hotel_id:
        parts.append(f"- hotel_id: `{hotel_id}`")
    if branch_id and not portfolio:
        parts.append(f"- current_branch: `{branch_id}`" + (f" ({branch_name})" if branch_name else ""))
    if portfolio:
        parts.append(
            "- Property scope: **portfolio (ALL)** — only hotels in this owner's group;"
            " never other tenants outside the group."
        )
    else:
        parts.append(
            "- STRICT DATA BOUNDARY: Only use data for the assigned hotel/branch above."
            " Never reveal or query other tenants' data."
        )
    if view_mode == "hotel_impersonation":
        parts.append(
            "- Note: a platform admin is **impersonating** this hotel;"
            " still enforce hotel data isolation for answers."
        )
    parts.append(
        "- Prefer hospitality / property operations APIs scoped to this hotel;"
        " refuse cross-tenant platform-admin actions."
    )
    return parts


def build_system_prompt(context: dict) -> str:
    """Build a system prompt that includes the current UI context, vocabulary, and resolved entities."""
    # Adaptive profile context (vocabulary overrides, business type)
    adaptive = context.get("adaptive_profile")
    if adaptive and adaptive.get("profile_name"):
        intro = f"You are an AI assistant embedded in a **{adaptive['profile_name']}** management application."
    elif context.get("domain"):
        intro = f"You are an AI assistant embedded in a **{context['domain']}** web application."
    else:
        intro = "You are an AI assistant embedded in a business web application."

    parts = [
        intro,
        "You help users understand their data, answer questions about records,",
        "and guide them through workflows.",
        "",
        "Keep responses concise and actionable. Use markdown formatting.",
        "When referring to specific records, mention their IDs.",
    ]

    parts.extend(_session_scope_block(context))

    tool_prompt = tool_catalog_prompt(context)
    if tool_prompt:
        parts.append(tool_prompt)

    # Vocabulary overrides from adaptive profile
    if adaptive:
        vocab = adaptive.get("vocabulary", {})
        if vocab:
            parts.append("\n**Terminology for this business:**")
            for standard, adapted in vocab.items():
                parts.append(f'- Use "{adapted}" instead of "{standard}"')

    skill = context.get("skill")
    if skill:
        parts.append(f"\nThe user is currently viewing the **{skill}** skill.")

    entity = context.get("entity")
    if entity:
        parts.append(f"They are looking at: **{entity}**")

    record_id = context.get("record_id") or context.get("recordId")
    if record_id:
        parts.append(f"Specific record: `{record_id}`")

    view = context.get("view")
    if view:
        parts.append(f"Current view: {view}")

    route = context.get("route")
    if route:
        parts.append(f"Current route: `{route}`")

    # Resolved entities from context resolution (C1)
    resolved = context.get("resolved_entities")
    if resolved and isinstance(resolved, list) and len(resolved) > 0:
        parts.append("\n**Resolved entities from the conversation:**")
        for ent in resolved:
            name = ent.get("name", "?")
            etype = ent.get("entity_type", "entity")
            conf = ent.get("confidence", 0)
            parts.append(f"- {etype}: **{name}** (id: {ent.get('id', '?')}, confidence: {conf:.0%})")

    # Action schema injection (E1) — gives AI knowledge of available actions
    if skill:
        try:
            schema = get_action_schema(skill)
            if schema:
                parts.append(f"\n**Available actions for {skill}:**")
                parts.append(schema)
                parts.append(_COMPOSITION_INSTRUCTIONS)
        except Exception:
            pass  # Non-critical

    return "\n".join(parts)


_COMPOSITION_INSTRUCTIONS = """
**Performing actions:**
When the user asks you to perform an action (create, add, update, submit, etc.):
1. Identify the matching action from the available actions above
2. Resolve parameters from the conversation context and resolved entities
3. Output a composition block that the UI can execute:

<composition>{"action":"add-customer","skill":"erpclaw","resolved_fields":[{"name":"customer-name","value":"Acme Corp"},{"name":"email","value":"info@acme.com"}],"confirmation":"Create customer Acme Corp?"}</composition>

Rules for composition blocks:
- Use exact action names from the available actions list
- Include all required parameters (marked with *)
- Use resolved entity IDs when referencing existing records
- Always include a human-readable "confirmation" field
- If you're missing required information, ask the user instead of guessing
"""


def _user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content") or ""
    return ""


async def single_chat(messages: list[dict], context: dict, max_tokens: int = 512) -> str:
    """Non-streaming AI call via the multi-LLM router. Returns response text."""
    system = build_system_prompt(context)
    router = get_router()
    request = ChatRequest(
        messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        system=system,
        max_tokens=max_tokens,
    )
    try:
        result = await router.complete(
            request,
            message=_user_message(messages),
            history_len=len(messages),
            context=context,
        )
        return result.text
    except Exception:
        return ""


async def stream_chat(
    messages: list[dict], context: dict
) -> AsyncGenerator[str, None]:
    """Stream AI response chunks via the multi-LLM router. Yields text deltas."""
    system = build_system_prompt(context)
    router = get_router()
    request = ChatRequest(
        messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        system=system,
        max_tokens=router.settings.max_tokens,
    )
    async for chunk in router.stream(
        request,
        message=_user_message(messages),
        history_len=len(messages),
        context=context,
    ):
        yield chunk
