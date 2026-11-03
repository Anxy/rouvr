"""Scoped os.environ for tests. Import as `from env_scope import scoped_env`.

Uniquely named: `conftest` cannot be imported by test modules (multiple
conftest.py files exist under this repo and shadow each other).
"""
from __future__ import annotations

import contextlib
import os


@contextlib.contextmanager
def scoped_env(**sets):
    """Temporarily set env vars (None value = pop key), restoring the
    previous values (or absence) afterwards. Use in module- and
    function-scoped fixtures that cannot use monkeypatch."""
    saved = {key: os.environ.get(key) for key in sets}
    try:
        for key, value in sets.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
