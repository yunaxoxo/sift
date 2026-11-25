"""Bridge between the Python pipeline and the Rust chunker.

All walking and chunking happens in ``sift._rust_core.chunk_directory``. This
module only calls it, and translates the two ways the native side can be "not ready"
into errors the CLI can explain:

* the extension was never built            -> :class:`NativeExtensionMissingError`
* a Rust function still contains ``todo!()`` -> :class:`StubNotImplementedError`
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol

from sift.errors import NativeExtensionMissingError, SiftError, StubNotImplementedError


class ChunkLike(Protocol):
    """Structural type shared by the Rust ``DocumentChunk`` and :class:`Chunk`."""

    @property
    def file_path(self) -> str: ...
    @property
    def chunk_index(self) -> int: ...
    @property
    def text(self) -> str: ...
    @property
    def start_line(self) -> int: ...
    @property
    def heading_context(self) -> str: ...


@dataclass(frozen=True)
class Chunk:
    """Python twin of the Rust ``DocumentChunk`` (same fields, same meaning)."""

    file_path: str
    chunk_index: int
    text: str
    start_line: int
    heading_context: str


def load_rust_core() -> ModuleType:
    """Import the compiled extension, or explain how to build it."""
    try:
        from sift import _rust_core
    except ImportError as exc:
        raise NativeExtensionMissingError(
            "the Rust extension sift._rust_core is not built.\n"
            "  Build it:  maturin develop --release\n"
            f"  (import error was: {exc})"
        ) from exc
    return _rust_core


def is_rust_todo(exc: BaseException) -> bool:
    """True if ``exc`` is the Python face of a Rust ``todo!()``.

    PyO3 turns a Rust panic into ``pyo3_runtime.PanicException``. That class
    derives from ``BaseException`` (so that ``except Exception`` cannot swallow
    a crash) and cannot be imported by name, hence the check on the type name.
    ``todo!()`` panics with the message "not yet implemented".
    """
    return type(exc).__name__ == "PanicException" and "not yet implemented" in str(exc)


@contextmanager
def rust_stub_guard() -> Iterator[None]:
    """Convert a ``todo!()`` panic into :class:`StubNotImplementedError`.

    Any other panic is a genuine bug in the Rust code and propagates untouched.
    """
    try:
        yield
    except BaseException as exc:
        if is_rust_todo(exc):
            detail = str(exc).removeprefix("not yet implemented").lstrip(": ")
            raise StubNotImplementedError(
                "a Rust stub in rust_core/src/ is still todo!()" + (f": {detail}" if detail else "")
            ) from None
        raise


def chunk_directory(
    root: Path,
    *,
    chunker: str,
    extensions: Sequence[str],
    max_chars: int,
    overlap: int,
) -> list[ChunkLike]:
    """Walk ``root`` and return every chunk, files in sorted path order."""
    if not root.is_dir():
        raise SiftError(f"not a directory: {root}")
    if chunker != "rust":
        raise SiftError(f"unknown chunker {chunker!r}; expected 'rust'")
    core = load_rust_core()
    with rust_stub_guard():
        return list(core.chunk_directory(str(root), list(extensions), max_chars, overlap))
