#!/usr/bin/env python3
"""Retrieval benchmark and CI regression gate for sift.

What it does, end to end:

1. Builds a **fresh** index of ``sample_docs/`` in a temporary directory, so it
   never touches your own ``.sift/`` index and every run starts equal.
2. Runs the 20 queries of ``eval/queries.json`` in each search mode
   (hybrid / lexical / semantic).
3. Reduces each result list to a ranking of *files* (first hit per file) and
   scores it against the ground truth: Recall@3, NDCG@5 and MRR.
4. Measures warm, in-process query latency and reports p50 / p95, total and
   per stage. "Warm" means the model is loaded and every query has run once
   before timing starts; cold-start cost is reported separately as model load.
5. Prints a Markdown table you can paste into the README, and writes the full
   numbers to ``eval/results/latest.json``.
6. With ``--check``, compares quality against ``eval/baseline.json`` and exits
   non-zero on a regression. With ``--update-baseline``, records the current
   numbers as the new baseline.

Typical use::

    python eval/benchmark.py                           # real model, publishable numbers
    python eval/benchmark.py --embedder hash --check   # fast, deterministic CI gate
    python eval/benchmark.py --update-baseline         # accept the current numbers
    python eval/benchmark.py --verbose                 # per-query ranks

Exit codes: 0 = ok, 1 = regression or error, 3 = a hand-written stub is still
empty (Rust ``todo!()`` or ``sift/ranking.py``), so nothing could be measured.

Numbers produced with ``--embedder hash`` are for regression testing only. That
embedder has no semantics; never publish its results.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ir_metrics import mean, ndcg_at_k, recall_at_k, reciprocal_rank

from sift import __version__, config, db
from sift.embeddings import Embedder, make_embedder
from sift.errors import EXIT_ERROR, EXIT_OK, EXIT_STUB, SiftError
from sift.indexer import IndexReport, index_directory
from sift.search import search
from sift.telemetry import QUERY_STAGES, summarize

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = Path(__file__).resolve().parent

RECALL_K = 3
NDCG_K = 5
QUALITY_METRICS = (f"recall@{RECALL_K}", f"ndcg@{NDCG_K}", "mrr")
# Chunks fetched per query. Must be deep enough that de-duplicating chunks to
# files still leaves at least NDCG_K distinct files.
RETRIEVAL_DEPTH = 100


# ---------------------------------------------------------------------------
# Loading and validating the ground truth
# ---------------------------------------------------------------------------


def load_queries(path: Path) -> list[dict]:
    """Read the graded query set and reject duplicate ids and queries with no relevant files."""
    queries = json.loads(path.read_text(encoding="utf-8"))["queries"]
    seen: set[str] = set()
    for entry in queries:
        if entry["id"] in seen:
            raise SiftError(f"{path}: duplicate query id {entry['id']}")
        seen.add(entry["id"])
        if not entry["query"].strip() or not entry["relevant"]:
            raise SiftError(f"{path}: query {entry['id']} needs a query and relevant files")
    return queries


def check_ground_truth(queries: Sequence[dict], indexed_files: set[str]) -> None:
    """Fail loudly if the ground truth names a file that is not in the corpus.

    Without this check a renamed note would silently turn into a permanent
    "miss" and drag every metric down.
    """
    for entry in queries:
        missing = sorted(set(entry["relevant"]) - indexed_files)
        if missing:
            raise SiftError(
                f"query {entry['id']} expects {missing}, which the index does not contain. "
                "Fix eval/queries.json or the --docs path."
            )


# ---------------------------------------------------------------------------
# Running the benchmark
# ---------------------------------------------------------------------------


def ranked_files(hits) -> list[str]:
    """Collapse chunk hits to a file ranking: each file at its best chunk's rank."""
    files: list[str] = []
    for hit in hits:
        name = Path(hit.file_path).name
        if name not in files:
            files.append(name)
    return files


def evaluate_mode(
    conn,
    queries: Sequence[dict],
    embedder: Embedder,
    mode: str,
    repeats: int,
) -> dict:
    """Run every query in one mode; return quality, latency and per-query detail."""

    def run(text: str):
        return search(
            conn,
            text,
            embedder=None if mode == "lexical" else embedder,
            mode=mode,
            top_k=RETRIEVAL_DEPTH,
            pool_size=RETRIEVAL_DEPTH,
            log=False,
        )

    # Warm-up pass, not measured: every query runs once first. The reported
    # latency is therefore steady-state. It excludes one-off costs such as the
    # OS page cache filling and the ML runtime compiling kernels for each new
    # input length, which otherwise show up as a few huge outliers in p95.
    for entry in queries:
        run(entry["query"])

    per_query = []
    samples: dict[str, list[float]] = {stage: [] for stage in QUERY_STAGES}
    for entry in queries:
        response = run(entry["query"])
        for attempt in range(repeats):
            timed = response if attempt == 0 else run(entry["query"])
            for stage in QUERY_STAGES:
                if stage in timed.timings:
                    samples[stage].append(timed.timings[stage])

        files = ranked_files(response.hits)
        relevance = entry["relevant"]
        first_relevant = next(
            (rank for rank, name in enumerate(files, start=1) if name in relevance), None
        )
        per_query.append(
            {
                "id": entry["id"],
                "type": entry.get("type", ""),
                "query": entry["query"],
                f"recall@{RECALL_K}": recall_at_k(files, relevance, RECALL_K),
                f"ndcg@{NDCG_K}": ndcg_at_k(files, relevance, NDCG_K),
                "rr": reciprocal_rank(files, relevance),
                "first_relevant_rank": first_relevant,
                "top_files": files[:NDCG_K],
            }
        )

    quality = {
        f"recall@{RECALL_K}": mean([q[f"recall@{RECALL_K}"] for q in per_query]),
        f"ndcg@{NDCG_K}": mean([q[f"ndcg@{NDCG_K}"] for q in per_query]),
        "mrr": mean([q["rr"] for q in per_query]),
    }
    latency = {}
    for stage, values in samples.items():
        summary = summarize(values)
        latency[stage] = None if summary is None else summary.as_dict()
    return {"quality": quality, "latency_ms": latency, "queries": per_query}


def run_benchmark(args: argparse.Namespace) -> dict:
    """Index ``--docs`` in a scratch database, run every query in every mode, return the results."""
    queries = load_queries(args.queries)
    embedder = make_embedder(args.embedder, args.model)

    with tempfile.TemporaryDirectory(prefix="sift-bench-") as workdir:
        conn = db.connect(Path(workdir) / "bench.db", create=True)
        try:
            report: IndexReport = index_directory(
                conn,
                args.docs,
                embedder,
                chunker=args.chunker,
                max_chars=args.max_chars,
                overlap=args.overlap,
            )
            indexed = {Path(row[0]).name for row in conn.execute("SELECT path FROM files")}
            check_ground_truth(queries, indexed)
            overview = db.overview(conn)
            modes = {
                mode: evaluate_mode(conn, queries, embedder, mode, args.repeats)
                for mode in args.modes
            }
        finally:
            conn.close()

    return {
        "profile": f"{embedder.kind}:{embedder.model}|{args.chunker}",
        "environment": {
            "sift_version": __version__,
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "embedder": f"{embedder.kind}:{embedder.model}",
            "chunker": args.chunker,
            "max_chars": args.max_chars,
            "overlap": args.overlap,
            "queries": len(queries),
            "repeats": args.repeats,
        },
        "corpus": {
            "files": overview["files"],
            "chunks": overview["chunks"],
            "avg_chunk_tokens": overview["avg_chunk_tokens"],
            "vocabulary": overview["vocabulary"],
        },
        "index_timings_ms": report.timings,
        "modes": modes,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def print_report(results: dict, *, verbose: bool) -> None:
    """Print quality and latency per mode as a table; ``verbose`` adds per-query detail."""
    env, corpus = results["environment"], results["corpus"]
    index = results["index_timings_ms"]
    print(f"sift {env['sift_version']} benchmark")
    print(f"  embedder  {env['embedder']}")
    print(f"  chunker   {env['chunker']} (max_chars={env['max_chars']}, overlap={env['overlap']})")
    print(
        f"  corpus    {corpus['files']} files, {corpus['chunks']} chunks, "
        f"avg {corpus['avg_chunk_tokens']} tokens/chunk"
    )
    print(f"  machine   {env['platform']}, Python {env['python']}")
    print(
        f"  indexing  chunk {_fmt(index.get('chunk_ms'))} ms · "
        f"model load {_fmt(index.get('model_load_ms'))} ms · "
        f"embed {_fmt(index.get('embed_ms'))} ms · "
        f"db write {_fmt(index.get('db_write_ms'))} ms"
    )
    print()

    print(f"### Retrieval quality and latency ({env['queries']} queries × {env['repeats']} runs)")
    print()
    print(f"| mode | Recall@{RECALL_K} | NDCG@{NDCG_K} | MRR | p50 (ms) | p95 (ms) |")
    print("|------|---------:|-------:|----:|---------:|---------:|")
    for mode, data in results["modes"].items():
        quality, total = data["quality"], data["latency_ms"]["total_ms"]
        print(
            f"| {mode} | {quality[f'recall@{RECALL_K}']:.3f} | {quality[f'ndcg@{NDCG_K}']:.3f} "
            f"| {quality['mrr']:.3f} | {_fmt(total and total['p50'])} "
            f"| {_fmt(total and total['p95'])} |"
        )
    print()

    for mode, data in results["modes"].items():
        print(f"### Stage latency, {mode} (ms)")
        print()
        print("| stage | p50 | p95 | mean |")
        print("|-------|----:|----:|-----:|")
        for stage in QUERY_STAGES:
            summary = data["latency_ms"][stage]
            if summary is None:
                continue
            print(
                f"| {stage.removesuffix('_ms')} | {summary['p50']:.2f} "
                f"| {summary['p95']:.2f} | {summary['mean']:.2f} |"
            )
        print()

    for mode, data in results["modes"].items():
        shown = [q for q in data["queries"] if verbose or q[f"recall@{RECALL_K}"] < 1.0]
        if not shown:
            continue
        title = (
            "Per-query results" if verbose else f"Queries missing a relevant file in top {RECALL_K}"
        )
        print(f"{title} ({mode}):")
        for q in shown:
            rank = q["first_relevant_rank"]
            print(
                f"  {q['id']} [{q['type']:<10}] first relevant at "
                f"{'#' + str(rank) if rank else 'none':<5} {q['query']!r}"
            )
            print(f"       top: {', '.join(q['top_files'][:RECALL_K]) or '(no results)'}")
        print()

    if env["embedder"].startswith("hash:"):
        print(
            "note: the hash embedder has no semantics. These numbers are a regression "
            "signal only; do not publish them.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Baseline handling (the CI gate)
# ---------------------------------------------------------------------------


def _quality_only(results: dict) -> dict:
    return {
        mode: {metric: round(data["quality"][metric], 4) for metric in QUALITY_METRICS}
        for mode, data in results["modes"].items()
    }


def update_baseline(path: Path, results: dict) -> None:
    """Record the current quality numbers as the baseline for this profile."""
    baseline = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    baseline.setdefault(results["profile"], {}).update(_quality_only(results))
    path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"baseline for profile {results['profile']!r} written to {path}")


def check_against_baseline(
    path: Path, results: dict, tolerance: float, max_p95_ms: float | None
) -> list[str]:
    """Return a list of human-readable regressions; empty means the gate passes."""
    problems: list[str] = []
    profile = results["profile"]
    baseline = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    expected = baseline.get(profile)
    if expected is None:
        return [
            f"no baseline for profile {profile!r} in {path}. Record one with "
            "--update-baseline (same --embedder and --chunker) and commit the file."
        ]

    for mode, data in results["modes"].items():
        if mode not in expected:
            problems.append(f"{mode}: no baseline recorded for this mode")
            continue
        for metric in QUALITY_METRICS:
            now, before = data["quality"][metric], expected[mode][metric]
            if now < before - tolerance:
                problems.append(
                    f"{mode} {metric}: {now:.4f} is below baseline {before:.4f} "
                    f"(tolerance {tolerance})"
                )
        total = data["latency_ms"]["total_ms"]
        if max_p95_ms is not None and total and total["p95"] > max_p95_ms:
            problems.append(f"{mode} p95 latency {total['p95']:.1f} ms exceeds {max_p95_ms} ms")
    return problems


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _modes(text: str) -> list[str]:
    modes = [part.strip() for part in text.split(",") if part.strip()]
    unknown = [mode for mode in modes if mode not in config.SEARCH_MODES]
    if unknown or not modes:
        raise argparse.ArgumentTypeError(
            f"expected a comma-separated subset of {','.join(config.SEARCH_MODES)}"
        )
    return modes


def build_parser() -> argparse.ArgumentParser:
    """Command-line options of the benchmark."""
    parser = argparse.ArgumentParser(
        description="sift retrieval benchmark and CI regression gate."
    )
    parser.add_argument("--docs", type=Path, default=REPO_ROOT / "sample_docs")
    parser.add_argument("--queries", type=Path, default=EVAL_DIR / "queries.json")
    parser.add_argument(
        "--embedder", choices=config.EMBEDDER_KINDS, default=config.embedder_from_env()
    )
    parser.add_argument("--model", default=config.model_from_env())
    parser.add_argument("--chunker", choices=config.CHUNKERS, default=config.DEFAULT_CHUNKER)
    parser.add_argument("--max-chars", type=int, default=config.DEFAULT_MAX_CHARS)
    parser.add_argument("--overlap", type=int, default=config.DEFAULT_OVERLAP)
    parser.add_argument("--modes", type=_modes, default=list(config.SEARCH_MODES))
    parser.add_argument(
        "--repeats", type=int, default=5, help="latency samples per query (default: 5)"
    )
    parser.add_argument("--baseline", type=Path, default=EVAL_DIR / "baseline.json")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if quality regressed against the baseline"
    )
    parser.add_argument(
        "--update-baseline", action="store_true", help="record the current quality as the baseline"
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.02,
        help="allowed absolute drop per metric before --check fails (default: 0.02)",
    )
    parser.add_argument(
        "--max-p95-ms",
        type=float,
        default=None,
        help="with --check, also fail if p95 total latency exceeds this. Off by "
        "default: shared CI runners are too noisy for a tight latency gate.",
    )
    parser.add_argument("--output", type=Path, default=EVAL_DIR / "results" / "latest.json")
    parser.add_argument("--verbose", action="store_true", help="print every query's result")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the benchmark: exit 0 on success, 3 while a stub is empty, 1 on error or regression."""
    args = build_parser().parse_args(argv)
    if args.repeats < 1:
        print("benchmark: --repeats must be at least 1", file=sys.stderr)
        return EXIT_ERROR

    try:
        results = run_benchmark(args)
    except NotImplementedError as exc:
        print(f"benchmark: not implemented yet: {exc}", file=sys.stderr)
        return EXIT_STUB
    except SiftError as exc:
        label = "not implemented yet" if exc.exit_code == EXIT_STUB else "error"
        print(f"benchmark: {label}: {exc}", file=sys.stderr)
        return exc.exit_code

    print_report(results, verbose=args.verbose)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"full results written to {args.output}", file=sys.stderr)

    if args.update_baseline:
        update_baseline(args.baseline, results)
    if args.check and not args.update_baseline:
        problems = check_against_baseline(args.baseline, results, args.tolerance, args.max_p95_ms)
        if problems:
            print("REGRESSION", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_ERROR
        print("baseline check passed", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
