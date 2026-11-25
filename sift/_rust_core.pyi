"""Type stubs for the compiled extension built from ``rust_core/src/lib.rs``.

Keep this file in sync with the ``#[pyclass]`` / ``#[pyfunction]`` items there:
type checkers and editors read this file, never the Rust source.
"""

from os import PathLike
from typing import final

__version__: str
DEFAULT_MAX_CHARS: int
DEFAULT_OVERLAP: int
DEFAULT_EXTENSIONS: list[str]

@final
class DocumentChunk:
    """One retrievable unit of text. Immutable; produced by the Rust chunker."""

    def __new__(
        cls,
        file_path: str,
        chunk_index: int,
        text: str,
        start_line: int,
        heading_context: str,
    ) -> DocumentChunk: ...
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
    def __len__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

def walk_files(
    root: str | PathLike[str],
    extensions: list[str] | None = None,
) -> list[str]: ...
def chunk_text(
    file_path: str,
    text: str,
    max_chars: int = 1200,
    overlap: int = 150,
) -> list[DocumentChunk]: ...
def chunk_directory(
    root: str | PathLike[str],
    extensions: list[str] | None = None,
    max_chars: int = 1200,
    overlap: int = 150,
) -> list[DocumentChunk]: ...
