"""Safe argument parser for ERPClaw skill scripts.

Replaces argparse.ArgumentParser with a version that outputs JSON errors
instead of printing to stderr and exiting with code 2. This ensures the
AI agent always gets machine-readable feedback when flags are wrong.
"""
import argparse
import json
import sys

TENANT_FLAGS = ("--tenant-id", "--company-id")


class SafeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that outputs JSON errors instead of stderr + exit(2).

    When argparse encounters invalid flags or missing required args, the
    default behavior is to print a message to stderr and call sys.exit(2).
    In OpenClaw's pipeline, stderr is invisible to the AI agent — it only
    sees stdout JSON. This subclass overrides error() to produce a JSON
    error response with the list of valid flags, so the agent can
    self-correct on retry.

    Every skill CLI accepts ``--tenant-id`` (the hotel's ``company.id``) so
    WebClaw can pass it on every action without each domain declaring it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_argument(
            "--tenant-id",
            default=None,
            dest="tenant_id",
            help="Hotel/company UUID (bound from the WebClaw session)",
        )

    def add_argument(self, *args, **kwargs):
        # Domain scripts that already declare --tenant-id keep working.
        flags = [a for a in args if isinstance(a, str) and a.startswith("-")]
        existing = {opt for action in self._actions for opt in action.option_strings}
        if flags and all(f in existing for f in flags):
            return None
        return super().add_argument(*args, **kwargs)

    def error(self, message):
        """Output JSON error with valid flags instead of stderr + exit(2)."""
        valid_flags = sorted(set(
            opt for action in self._actions
            for opt in action.option_strings
            if opt.startswith("--")
        ))

        # Extract valid action choices if --action has choices=
        action_choices = []
        for action in self._actions:
            if "--action" in action.option_strings and action.choices:
                action_choices = sorted(action.choices)
                break

        data = {"status": "error", "message": message}
        if action_choices:
            data["valid_actions"] = action_choices
        data["valid_flags"] = valid_flags

        print(json.dumps(data, indent=2))
        sys.exit(1)


def add_tenant_args(parser: argparse.ArgumentParser) -> None:
    """Ensure ``--tenant-id`` / ``--company-id`` exist (idempotent)."""
    existing = {opt for action in parser._actions for opt in action.option_strings}
    if "--tenant-id" not in existing:
        parser.add_argument("--tenant-id", default=None, dest="tenant_id")
    if "--company-id" not in existing:
        parser.add_argument("--company-id", default=None, dest="company_id")


def _consume_known_tenant_flags(unknown: list[str]) -> list[str]:
    """Drop leftover tenant flags so domain scripts never reject them."""
    out = []
    skip_next = False
    for token in unknown:
        if skip_next:
            skip_next = False
            continue
        if token in TENANT_FLAGS:
            skip_next = True
            continue
        if token.startswith("--tenant-id=") or token.startswith("--company-id="):
            continue
        out.append(token)
    return out


def check_unknown_args(parser, unknown):
    """Reject unknown flags with a JSON error listing valid flags.

    Call this after parser.parse_known_args() to catch typos like
    --start-date (should be --start-of-care). Without this, unknown
    flags are silently ignored and the action fails with a confusing
    "missing required field" error.

    ``--tenant-id`` / ``--company-id`` are always accepted so hotel
    isolation flags can ride on every action payload.
    """
    unknown = _consume_known_tenant_flags(list(unknown or []))
    if not unknown:
        return

    valid_flags = sorted(set(
        opt for action in parser._actions
        for opt in action.option_strings
        if opt.startswith("--")
    ))

    data = {
        "status": "error",
        "message": f"Unknown flags: {', '.join(unknown)}. Check flag names and try again.",
        "valid_flags": valid_flags,
    }
    print(json.dumps(data, indent=2))
    sys.exit(1)
