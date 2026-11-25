"""Command-line interface: ``sift index | search | stats``.

Exit codes (see ``sift/errors.py``):

== =========================================================================
0  success
1  expected failure (no index, bad directory, model mismatch, ...)
2  bad command line
3  a hand-written piece of the project is still a stub (``todo!()`` in Rust
   or ``NotImplementedError`` in ``sift/ranking.py``)
== =========================================================================

Results go to stdout; diagnostics and timings go to stderr, so
``sift search ... --json | jq`` stays clean.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from sift import __version__, config, db
from sift.embeddings import make_embedder
from sift.errors import EXIT_OK, EXIT_STUB, SiftError
from sift.indexer import IndexReport, index_directory
from sift.search import SearchResponse, search
from sift.telemetry import QUERY_STAGES, summarize

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text}")
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must not be negative, got {text}")
    return value


def _extension_list(text: str) -> list[str]:
    extensions = [part.strip().lstrip(".").lower() for part in text.split(",")]
    extensions = [ext for ext in extensions if ext]
    if not extensions:
        raise argparse.ArgumentTypeError("expected a comma-separated list such as md,markdown")
    return extensions


def build_parser() -> argparse.ArgumentParser:
    # --db is accepted both before and after the subcommand. SUPPRESS on the
    # shared parent keeps a subcommand default from overwriting a value given
    # before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        type=Path,
        default=argparse.SUPPRESS,
        metavar="PATH",
        help=f"index database (default: $SIFT_DB or {config.DEFAULT_DB_PATH})",
    )

    parser = argparse.ArgumentParser(
        prog="sift",
        description="Local-first hybrid search for Markdown notes and codebases.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"sift {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # --- index ---------------------------------------------------------
    index = commands.add_parser(
        "index",
        parents=[common],
        help="chunk, embed and store every matching file under a directory",
        description="Chunk, embed and store every matching file under DIR (incremental).",
    )
    index.add_argument("directory", type=Path, metavar="DIR")
    index.add_argument(
        "--chunker",
        choices=config.CHUNKERS,
        default=config.DEFAULT_CHUNKER,
        help="chunker implementation (default: %(default)s)",
    )
    index.add_argument(
        "--embedder",
        choices=config.EMBEDDER_KINDS,
        default=config.embedder_from_env(),
        help="embedding backend; 'hash' is an offline test double (default: %(default)s)",
    )
    index.add_argument(
        "--model",
        default=config.model_from_env(),
        help="sentence-transformers model name (default: %(default)s)",
    )
    index.add_argument(
        "--ext",
        type=_extension_list,
        default=list(config.DEFAULT_EXTENSIONS),
        metavar="LIST",
        help="comma-separated file extensions (default: md,markdown)",
    )
    index.add_argument("--max-chars", type=_positive_int, default=config.DEFAULT_MAX_CHARS)
    index.add_argument("--overlap", type=_non_negative_int, default=config.DEFAULT_OVERLAP)
    index.add_argument("--batch-size", type=_positive_int, default=config.DEFAULT_BATCH_SIZE)
    index.add_argument(
        "--rebuild",
        action="store_true",
        help="discard the existing index first (required when changing the model)",
    )
    index.add_argument("--json", action="store_true", help="print the run report as JSON")
    index.set_defaults(handler=cmd_index)

    # --- search --------------------------------------------------------
    find = commands.add_parser(
        "search",
        parents=[common],
        help="query the index",
        description="Query the index with BM25 + embeddings fused by RRF.",
    )
    find.add_argument("query")
    find.add_argument("-k", "--top-k", type=_positive_int, default=config.DEFAULT_TOP_K)
    find.add_argument(
        "--mode",
        choices=config.SEARCH_MODES,
        default=config.DEFAULT_MODE,
        help="hybrid = BM25 + vectors via RRF; lexical / semantic = one retriever "
        "only, for ablation (default: %(default)s)",
    )
    find.add_argument(
        "--pool",
        type=_positive_int,
        default=config.DEFAULT_POOL_SIZE,
        help="candidates each retriever feeds into fusion (default: %(default)s)",
    )
    find.add_argument("--rrf-k", type=_non_negative_int, default=None, help="RRF constant k")
    find.add_argument(
        "--explain",
        action="store_true",
        help="show each hit's rank and raw score in both retrievers",
    )
    find.add_argument("--no-log", action="store_true", help="do not record this query's timings")
    find.add_argument("--json", action="store_true", help="print hits and timings as JSON")
    find.set_defaults(handler=cmd_search)

    # --- stats ---------------------------------------------------------
    stats = commands.add_parser(
        "stats",
        parents=[common],
        help="index size, last index run, and query latency percentiles",
        description="Show index size, the last index run's stage timings, and "
        "p50/p95 latency per query stage.",
    )
    stats.add_argument(
        "--last",
        type=_positive_int,
        default=config.DEFAULT_STATS_WINDOW,
        metavar="N",
        help="summarise the N most recent queries (default: %(default)s)",
    )
    stats.add_argument("--json", action="store_true", help="print everything as JSON")
    stats.set_defaults(handler=cmd_stats)

    # --- ui ------------------------------------------------------------
    ui = commands.add_parser(
        "ui",
        parents=[common],
        help="open the local web inspector (search playground + benchmark dashboard)",
        description="Serve the web inspector on localhost. Falls back to bundled preview "
        "data until an index exists and the ranking functions are implemented.",
    )
    ui.add_argument("--host", default="127.0.0.1", help="interface to bind (default: %(default)s)")
    ui.add_argument("--port", type=_non_negative_int, default=8765, help="0 picks a free port")
    ui.add_argument("--open", action="store_true", help="open a browser tab")
    ui.set_defaults(handler=cmd_ui)

    return parser


def _db_path(args: argparse.Namespace) -> Path:
    return getattr(args, "db", None) or config.db_path_from_env()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _ms(value: float | None, width: int = 0) -> str:
    text = "-" if value is None else f"{value:,.1f} ms"
    return text.rjust(width)


def _display_path(path: str) -> str:
    """Show paths relative to the working directory when they are inside it."""
    try:
        relative = os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return path
    return path if relative.startswith("..") else relative


def _snippet(text: str, limit: int = 220) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _emit_json(payload: object) -> None:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# sift index
# ---------------------------------------------------------------------------


def cmd_index(args: argparse.Namespace) -> int:
    """``sift index``: walk, chunk, embed and store a directory, re-embedding only changed files."""
    root: Path = args.directory
    if not root.is_dir():
        raise SiftError(f"not a directory: {root}")
    db_path = _db_path(args)

    conn = db.connect(db_path, create=True)
    try:
        embedder = make_embedder(args.embedder, args.model, batch_size=args.batch_size)
        report = index_directory(
            conn,
            root,
            embedder,
            chunker=args.chunker,
            extensions=args.ext,
            max_chars=args.max_chars,
            overlap=args.overlap,
            rebuild=args.rebuild,
        )
    finally:
        conn.close()

    if args.json:
        _emit_json({"db": str(db_path), **report.as_dict()})
    else:
        _print_index_report(report, db_path)
    return EXIT_OK


def _print_index_stages(timings: dict[str, float | None], chunker: str, embedded: int) -> None:
    embed_ms = timings.get("embed_ms")
    per_chunk = f"   ({embed_ms / embedded:.2f} ms/chunk)" if embed_ms and embedded else ""
    print(f"  {'chunk (' + chunker + ')':<18}{_ms(timings.get('chunk_ms'), 14)}")
    if timings.get("model_load_ms") is not None:
        print(f"  {'model load':<18}{_ms(timings.get('model_load_ms'), 14)}")
    print(f"  {'embed':<18}{_ms(embed_ms, 14)}{per_chunk}")
    print(f"  {'db write':<18}{_ms(timings.get('db_write_ms'), 14)}")
    print(f"  {'total':<18}{_ms(timings.get('total_ms'), 14)}")


def _print_index_report(report: IndexReport, db_path: Path) -> None:
    print(f"Indexed {_display_path(report.root)} -> {db_path}")
    print(
        f"  {'files':<18}{report.files_seen} seen · {report.files_indexed} indexed · "
        f"{report.files_unchanged} unchanged · {report.files_removed} removed"
    )
    print(f"  {'chunks':<18}{report.chunks_seen} seen · {report.chunks_embedded} embedded")
    print(f"  {'embedder':<18}{report.embedder}")
    print("Stage timings")
    _print_index_stages(dict(report.timings), report.chunker, report.chunks_embedded)
    if report.files_seen == 0:
        print("warning: no matching files found; check DIR and --ext", file=sys.stderr)


# ---------------------------------------------------------------------------
# sift search
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> int:
    """``sift search``: query the index and print the hits."""
    conn = db.connect(_db_path(args))
    try:
        embedder = None
        if args.mode != "lexical":
            kind = db.get_meta(conn, "embedder_kind")
            if kind is None:
                raise SiftError("the index is empty. Run:  sift index <dir>")
            embedder = make_embedder(kind, db.get_meta(conn, "embedding_model"))

        options = {} if args.rrf_k is None else {"rrf_k": args.rrf_k}
        response = search(
            conn,
            args.query,
            embedder=embedder,
            mode=args.mode,
            top_k=args.top_k,
            pool_size=max(args.pool, args.top_k),
            log=not args.no_log,
            **options,
        )
    finally:
        conn.close()

    if args.json:
        _emit_json(response.as_dict())
    else:
        _print_search_response(response, explain=args.explain)
    return EXIT_OK


def _print_search_response(response: SearchResponse, *, explain: bool) -> None:
    if not response.hits:
        print("No results.")
    for hit in response.hits:
        print(f"{hit.rank:>2}. {_display_path(hit.file_path)}:{hit.start_line}")
        if hit.heading_context:
            print(f"    {hit.heading_context}")
        print(f"    {_snippet(hit.text)}")
        if explain:
            parts = [f"score {hit.score:.6f}"]
            if hit.lexical_rank is not None:
                parts.append(f"lexical #{hit.lexical_rank} (bm25 {hit.lexical_score:.3f})")
            if hit.semantic_rank is not None:
                parts.append(f"semantic #{hit.semantic_rank} (cos {hit.semantic_score:.3f})")
            print(f"    {' · '.join(parts)}")
        print()

    timings = response.timings
    stages = [
        f"{name.removesuffix('_ms')} {timings[name]:.1f} ms"
        for name in QUERY_STAGES
        if name in timings
    ]
    print(f"{len(response.hits)} hits · {response.mode} · " + " · ".join(stages), file=sys.stderr)
    if response.model_load_ms is not None:
        print(
            f"model load {response.model_load_ms:,.1f} ms (once per process, not in total)",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# sift stats
# ---------------------------------------------------------------------------


def _collect_stats(conn: sqlite3.Connection, db_path: Path, window: int) -> dict[str, object]:
    rows = db.recent_query_timings(conn, window)
    latency = {}
    for stage in QUERY_STAGES:
        summary = summarize(row[stage] for row in rows)
        latency[stage] = None if summary is None else summary.as_dict()
    last_run = db.last_index_run(conn)
    return {
        "db": str(db_path),
        "db_bytes": db_path.stat().st_size,
        "index": db.overview(conn),
        "last_index_run": None if last_run is None else dict(last_run),
        "query_latency": {"window": len(rows), "stages_ms": latency},
    }


def cmd_stats(args: argparse.Namespace) -> int:
    """``sift stats``: index overview and recent query latency."""
    db_path = _db_path(args)
    conn = db.connect(db_path)
    try:
        stats = _collect_stats(conn, db_path, args.last)
    finally:
        conn.close()

    if args.json:
        _emit_json(stats)
    else:
        _print_stats(stats)
    return EXIT_OK


def _print_stats(stats: dict) -> None:
    index = stats["index"]
    print(f"Index  {stats['db']}  ({stats['db_bytes'] / 1024:,.0f} KiB)")
    print(f"  {'files':<18}{index['files']:,}")
    print(f"  {'chunks':<18}{index['chunks']:,}   (avg {index['avg_chunk_tokens']} tokens)")
    print(f"  {'vocabulary':<18}{index['vocabulary']:,} terms")
    if index["embedding_model"]:
        print(
            f"  {'embedder':<18}{index['embedder']}:{index['embedding_model']} "
            f"({index['embedding_dim']}-d)"
        )
    else:
        print(f"  {'embedder':<18}-")

    run = stats["last_index_run"]
    print()
    if run is None:
        print("Last index run  none recorded")
    else:
        print(f"Last index run  {run['started_at']}")
        print(f"  {'root':<18}{run['root']}")
        print(
            f"  {'files':<18}{run['files_seen']} seen · {run['files_indexed']} indexed · "
            f"{run['files_unchanged']} unchanged · {run['files_removed']} removed"
        )
        print(f"  {'chunks':<18}{run['chunks_seen']} seen · {run['chunks_embedded']} embedded")
        _print_index_stages(run, run["chunker"], run["chunks_embedded"])

    latency = stats["query_latency"]
    print()
    print(f"Query latency  last {latency['window']} of {index['queries_logged']:,} logged queries")
    if latency["window"] == 0:
        print('  no queries logged yet. Run:  sift search "<query>"')
        return
    print(f"  {'stage':<10}{'n':>6}{'p50':>13}{'p95':>13}{'mean':>13}{'max':>13}")
    for stage in QUERY_STAGES:
        summary = latency["stages_ms"][stage]
        label = stage.removesuffix("_ms")
        if summary is None:
            print(f"  {label:<10}{0:>6}{'-':>13}{'-':>13}{'-':>13}{'-':>13}")
            continue
        print(
            f"  {label:<10}{summary['count']:>6}"
            f"{_ms(summary['p50'], 13)}{_ms(summary['p95'], 13)}"
            f"{_ms(summary['mean'], 13)}{_ms(summary['max'], 13)}"
        )


# ---------------------------------------------------------------------------
# sift ui
# ---------------------------------------------------------------------------


def cmd_ui(args: argparse.Namespace) -> int:
    """``sift ui``: serve the local web inspector."""
    from sift.ui.server import serve  # imported lazily: only this command needs it

    return serve(_db_path(args), args.host, args.port, open_browser=args.open)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code (never calls ``sys.exit``)."""
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except SiftError as exc:
        label = "not implemented yet" if exc.exit_code == EXIT_STUB else "error"
        print(f"sift: {label}: {exc}", file=sys.stderr)
        return exc.exit_code
    except NotImplementedError as exc:
        # Raised by the stubs in sift/ranking.py.
        print(f"sift: not implemented yet: {exc}", file=sys.stderr)
        return EXIT_STUB
    except KeyboardInterrupt:
        print("sift: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
