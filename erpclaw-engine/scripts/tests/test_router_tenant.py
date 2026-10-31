"""Router accepts tenant_id on every action payload and binds env."""
import importlib.util
import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROUTER_PATH = os.path.join(os.path.dirname(_TESTS_DIR), "db_query.py")


def _load_router():
    spec = importlib.util.spec_from_file_location("erpclaw_router_tenant", _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROUTER = _load_router()


def test_bind_tenant_injects_both_flags(monkeypatch):
    monkeypatch.setenv("ERPCLAW_TENANT_ID", "hotel-a")
    out = ROUTER._bind_tenant_argv(["--action", "list-customers", "--limit", "20"])
    assert "--tenant-id" in out
    assert out[out.index("--tenant-id") + 1] == "hotel-a"
    assert "--company-id" in out
    assert out[out.index("--company-id") + 1] == "hotel-a"


def test_bind_tenant_from_payload_sets_env(monkeypatch):
    monkeypatch.delenv("ERPCLAW_TENANT_ID", raising=False)
    out = ROUTER._bind_tenant_argv([
        "--action", "list-customers", "--tenant-id", "hotel-b",
    ])
    assert os.environ["ERPCLAW_TENANT_ID"] == "hotel-b"
    assert "--company-id" in out
    assert out[out.index("--company-id") + 1] == "hotel-b"


def test_bind_tenant_mismatch_exits(monkeypatch):
    monkeypatch.setenv("ERPCLAW_TENANT_ID", "hotel-a")
    with pytest.raises(SystemExit):
        ROUTER._bind_tenant_argv(["--action", "list-customers", "--company-id", "hotel-b"])


def test_strip_router_flags_still_drops_user_confirmed(monkeypatch):
    monkeypatch.setenv("ERPCLAW_TENANT_ID", "hotel-a")
    out = ROUTER._strip_router_flags(["--action", "status", "--user-confirmed"])
    assert "--user-confirmed" not in out
    assert "--tenant-id" in out
