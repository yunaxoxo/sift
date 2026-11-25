"""Exception types the CLI knows how to present.

Anything that is not a ``SiftError`` is a bug and is allowed to surface as a
traceback.
"""

from __future__ import annotations

# Process exit codes used by ``sift`` and by ``eval/benchmark.py``.
EXIT_OK = 0
EXIT_ERROR = 1  # expected, user-facing failure (also: benchmark regression)
EXIT_USAGE = 2  # argparse's own code for bad command lines
EXIT_STUB = 3  # a hand-written piece of the project is still a stub


class SiftError(Exception):
    """An expected failure with a message meant for the user (exit code 1)."""

    exit_code = EXIT_ERROR


class IndexNotFoundError(SiftError):
    """No index database at the requested path."""


class NativeExtensionMissingError(SiftError):
    """``sift._rust_core`` could not be imported: it has not been built."""


class StubNotImplementedError(SiftError):
    """A hand-written piece (Rust ``todo!()`` or ranking stub) is not filled in yet.

    Carries exit code 3 so scripts and CI can tell "not written yet" apart from
    "written and broken".
    """

    exit_code = EXIT_STUB
