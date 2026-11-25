"""Per-stage latency measurement and percentile summaries.

Two pieces:

* :class:`StageTimer` measures named stages of one pipeline run with the
  monotonic high-resolution clock. The indexer and the search pipeline each
  create one per run and persist the result (tables ``index_runs`` and
  ``query_log``).
* :func:`percentile` / :func:`summarize` turn many persisted runs into the
  p50 / p95 numbers shown by ``sift stats`` and ``eval/benchmark.py``.

Why percentiles instead of the mean: latency distributions have a long right
tail (cold caches, GC pauses, a model warming up). The mean hides that tail;
p95 is the latency that 1 in 20 queries is slower than.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass

# Stage names. They double as column names in the telemetry tables, so keep
# them in sync with db.py.
INDEX_STAGES: tuple[str, ...] = ("chunk_ms", "embed_ms", "db_write_ms", "total_ms")
QUERY_STAGES: tuple[str, ...] = ("embed_ms", "lexical_ms", "vector_ms", "fusion_ms", "total_ms")


class StageTimer:
    """Accumulates wall-clock milliseconds per named stage.

    >>> timer = StageTimer()
    >>> with timer.stage("lexical_ms"):
    ...     pass
    >>> timer.get("lexical_ms") >= 0.0
    True

    Entering the same stage twice adds to it, so a stage can be split across
    several ``with`` blocks.
    """

    def __init__(self) -> None:
        self._ms: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time the ``with`` block with a monotonic nanosecond clock and add it to ``name``."""
        start = time.perf_counter_ns()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter_ns() - start) / 1e6)

    def add(self, name: str, ms: float) -> None:
        """Add ``ms`` to the stage ``name``."""
        self._ms[name] = self._ms.get(name, 0.0) + ms

    def get(self, name: str) -> float | None:
        """Milliseconds recorded for ``name``; ``None`` if the stage never ran."""
        return self._ms.get(name)

    def as_dict(self) -> dict[str, float]:
        return dict(self._ms)


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q``-th percentile (0 <= q <= 100) of ``values``. See specification section 6.5.

    Raises:
        ValueError: As specified in section 6.5.
    """
    raise NotImplementedError("sift/telemetry.py: percentile (IMPLEMENTATION_GUIDE.md section 6)")


@dataclass(frozen=True)
class LatencySummary:
    """Summary of one stage across many runs. All durations in milliseconds."""

    count: int
    mean: float
    p50: float
    p95: float
    max: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def summarize(values: Iterable[float | None]) -> LatencySummary | None:
    """Summarise durations, ignoring ``None`` (stage did not run for that query).

    Returns ``None`` when nothing was recorded, so callers can print a dash
    instead of inventing a zero.
    """
    samples = [float(v) for v in values if v is not None]
    if not samples:
        return None
    return LatencySummary(
        count=len(samples),
        mean=sum(samples) / len(samples),
        p50=percentile(samples, 50.0),
        p95=percentile(samples, 95.0),
        max=max(samples),
    )
