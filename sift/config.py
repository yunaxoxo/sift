"""Defaults and environment overrides, in one place.

Environment variables:

``SIFT_DB``        path of the index database (default ``.sift/index.db``)
``SIFT_EMBEDDER``  default embedder kind for ``sift index``
``SIFT_MODEL``     default embedding model for ``sift index``
``SIFT_OFFLINE``   set to ``1`` to forbid any model download (cache only)
"""

from __future__ import annotations

import os
from pathlib import Path

# --- storage ---------------------------------------------------------------
DEFAULT_DB_PATH = Path(".sift") / "index.db"

# --- chunking (mirrors DEFAULT_* in rust_core/src/lib.rs) -------------------
DEFAULT_EXTENSIONS: tuple[str, ...] = ("md", "markdown")
DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP = 150
CHUNKERS: tuple[str, ...] = ("rust",)
DEFAULT_CHUNKER = "rust"

# --- embeddings --------------------------------------------------------------
EMBEDDER_KINDS: tuple[str, ...] = ("sentence-transformers", "onnx", "hash")
DEFAULT_EMBEDDER = "sentence-transformers"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_BATCH_SIZE = 32
HASH_EMBEDDING_DIM = 256

# --- search ------------------------------------------------------------------
SEARCH_MODES: tuple[str, ...] = ("hybrid", "lexical", "semantic")
DEFAULT_MODE = "hybrid"
DEFAULT_TOP_K = 5
# How many candidates each retriever hands to the fusion step.
DEFAULT_POOL_SIZE = 50

# --- telemetry ---------------------------------------------------------------
# `sift stats` summarises this many of the most recent queries.
DEFAULT_STATS_WINDOW = 200
# query_log is trimmed to this many rows so the database cannot grow unbounded.
QUERY_LOG_MAX_ROWS = 5000


def db_path_from_env() -> Path:
    """The index path: ``SIFT_DB``, else ``.sift/index.db``."""
    return Path(os.environ.get("SIFT_DB") or DEFAULT_DB_PATH)


def embedder_from_env() -> str:
    """The embedder kind: ``SIFT_EMBEDDER``, else the default."""
    return os.environ.get("SIFT_EMBEDDER") or DEFAULT_EMBEDDER


def model_from_env() -> str:
    """The embedding model: ``SIFT_MODEL``, else the default."""
    return os.environ.get("SIFT_MODEL") or DEFAULT_MODEL


def offline_from_env() -> bool:
    """True when ``SIFT_OFFLINE`` forbids any model download."""
    return os.environ.get("SIFT_OFFLINE", "").strip().lower() in {"1", "true", "yes"}
