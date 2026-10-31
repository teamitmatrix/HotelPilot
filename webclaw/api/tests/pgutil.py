"""Postgres connection helpers for tests (replaces sqlite3.connect(path)).

Captures the real ``db.get_connection`` / ``db.get_skill_db`` at import time so
fixtures that monkeypatch those names and delegate back here cannot recurse.
"""
from __future__ import annotations

import db as _db

# Bound before any test monkeypatch.
_real_get_connection = _db.get_connection
_real_get_skill_db = _db.get_skill_db


def connect_web(_ignored=None):
    """Open the platform (webclaw schema) connection."""
    return _real_get_connection()


def connect_erp(_ignored=None):
    """Open the ERP / hospitality (public schema) skill connection."""
    return _real_get_skill_db("erpclaw")
