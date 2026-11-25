"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.chunking import is_rust_todo

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DOCS = REPO_ROOT / "sample_docs"


def call_or_skip(fn, *args, **kwargs):
    """Call a hand-written function; skip the test if it is still a stub.

    This is what makes the contract tests double as a to-do list: run
    ``pytest -ra`` and every skipped test names the stub it is waiting for.
    The moment a stub is implemented its tests start running for real.

    Two kinds of "not written yet" are recognised:

    * ``NotImplementedError`` from ``sift/ranking.py``
    * a Rust ``todo!()``, which reaches Python as ``PanicException``

    Anything else, including a genuine Rust panic, fails the test as usual.
    """
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as exc:
        pytest.skip(f"stub: {exc}")
    except BaseException as exc:
        if is_rust_todo(exc):
            pytest.skip(f"Rust stub: {exc}")
        raise
