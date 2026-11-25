"""Shared fixtures.

Walking and chunking live in Rust. Until those stubs are filled in, the
pipeline tests index through a test double that makes one chunk per file from
an explicit list of file names, so they exercise everything except the real
chunker. Mark a test ``native_chunker`` to run it against the real one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift import indexer
from sift.chunking import Chunk, ChunkLike


def one_chunk_per_file(
    root: Path, *, chunker: str, extensions, max_chars: int, overlap: int
) -> list[ChunkLike]:
    chunks: list[ChunkLike] = []
    for path in sorted(root.glob("**/*.md")):
        text = path.read_text(encoding="utf-8")
        if text.strip():
            chunks.append(Chunk(str(path), 0, text, 1, ""))
    return chunks


@pytest.fixture(autouse=True)
def baseline_chunker(request, monkeypatch):
    if request.node.get_closest_marker("native_chunker") is None:
        monkeypatch.setattr(indexer, "chunk_directory", one_chunk_per_file)
