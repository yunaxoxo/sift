"""HTTP server and JSON bridge behind ``sift ui``. Standard library only.

Threading: :class:`ThreadingHTTPServer` gives each request its own thread.
SQLite connections are opened per request (a connection must not cross
threads). The embedding model is loaded once and shared, and a lock
serialises searches so the model is never called concurrently. That keeps the
stage timings honest and sidesteps thread-safety questions in PyTorch.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import sqlite3
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable, Generator
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from sift import __version__, config, db, ranking
from sift.chunking import is_rust_todo
from sift.embeddings import Embedder, make_embedder
from sift.errors import SiftError, StubNotImplementedError
from sift.search import search
from sift.telemetry import QUERY_STAGES, summarize
from sift.tokenizer import tokenize

UI_DIR = Path(__file__).resolve().parent
STATIC_DIR = UI_DIR / "static"
PREVIEW_PATH = UI_DIR / "preview.json"
# Relative to the working directory, like the default index (.sift/index.db):
# run `sift ui` from the project root.
BENCHMARK_RESULTS = Path("eval") / "results" / "latest.json"

MAX_TOP_K = 50
MAX_QUERY_CHARS = 500
MAX_DOCUMENT_BYTES = 1_000_000

JSON = dict[str, Any]


# ---------------------------------------------------------------------------
# Which hand-written pieces exist yet
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _quiet_stderr() -> Generator[None]:
    """Silence fd 2 while probing a Rust ``todo!()`` (the panic hook prints there)."""
    sys.stderr.flush()
    saved = os.dup(2)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), 2)
            yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def _probe(call: Callable[[], object]) -> str:
    """``implemented`` | ``stub`` | ``error`` for one hand-written function."""
    try:
        with _quiet_stderr():
            call()
    except NotImplementedError:
        return "stub"
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        return "stub" if is_rust_todo(exc) else "error"
    return "implemented"


def probe_stubs() -> JSON:
    """Call each stub with trivial input. Instant, and never touches the index."""
    stats = ranking.CorpusStats(num_docs=1, avg_doc_len=1.0, doc_freqs={"a": 1})
    result: JSON = {
        "ranking": {
            "compute_bm25": _probe(lambda: ranking.compute_bm25(["a"], ["a"], stats)),
            "cosine_similarity": _probe(lambda: ranking.cosine_similarity([1.0], [1.0])),
            "reciprocal_rank_fusion": _probe(lambda: ranking.reciprocal_rank_fusion([1], [2])),
        }
    }
    try:
        from sift import _rust_core as core
    except ImportError:
        result["rust"] = {"built": False}
        return result
    with tempfile.TemporaryDirectory() as empty:
        result["rust"] = {
            "built": True,
            "walk_files": _probe(lambda: core.walk_files(empty)),
            "chunk_document": _probe(lambda: core.chunk_text("probe.md", "# Probe\n\nx\n")),
        }
    return result


# ---------------------------------------------------------------------------
# Application state shared by all request threads
# ---------------------------------------------------------------------------


class Inspector:
    """The data behind the UI: live index results when they exist, otherwise the bundled sample."""
    def __init__(self, db_path: Path, benchmark_path: Path = BENCHMARK_RESULTS) -> None:
        self.db_path = db_path
        self.benchmark_path = benchmark_path
        self.preview: JSON = json.loads(PREVIEW_PATH.read_text(encoding="utf-8"))
        self._search_lock = threading.Lock()
        self._embedder: Embedder | None = None
        self._embedder_key: tuple[str, str | None] | None = None
        self._stubs: JSON | None = None
        self._stubs_lock = threading.Lock()

    def stubs(self) -> JSON:
        """Which stubs are still unwritten, probed once and cached."""
        with self._stubs_lock:
            if self._stubs is None:
                self._stubs = probe_stubs()
            return self._stubs

    def _connect(self) -> sqlite3.Connection | None:
        """An open connection to a non-empty index, or ``None``."""
        try:
            conn = db.connect(self.db_path)
        except SiftError:
            return None
        if db.corpus_size(conn)[0] == 0:
            conn.close()
            return None
        return conn

    def _embedder_for(self, conn: sqlite3.Connection) -> Embedder:
        kind = db.get_meta(conn, "embedder_kind") or config.DEFAULT_EMBEDDER
        model = db.get_meta(conn, "embedding_model")
        if self._embedder is None or self._embedder_key != (kind, model):
            self._embedder = make_embedder(kind, model)
            self._embedder_key = (kind, model)
        return self._embedder

    def _preview_reason(self, conn: sqlite3.Connection | None) -> str:
        if conn is None:
            return f"No index at {self.db_path}. Run `sift index <dir>` to search your own notes."
        return "A ranking function in sift/ranking.py is still a stub."

    # --- endpoints -----------------------------------------------------------

    def status(self) -> JSON:
        """Corpus, last index run, example queries and where each answer comes from."""
        conn = self._connect()
        try:
            overview = db.overview(conn) if conn else None
            last_run = db.last_index_run(conn) if conn else None
        finally:
            if conn:
                conn.close()
        stubs = self.stubs()
        ranking_ready = all(v == "implemented" for v in stubs["ranking"].values())
        live = overview is not None and ranking_ready
        if last_run is not None:
            index_run = {"source": "live", **dict(last_run)}
        else:
            index_run = {"source": "preview", **self.preview["stats"]["last_index_run"]}
        return {
            "version": __version__,
            "db_path": str(self.db_path),
            "index": overview,
            "corpus": overview if overview is not None else self.preview["stats"]["index"],
            "index_run": index_run,
            "stubs": stubs,
            "search_source": "live" if live else "preview",
            "benchmark_source": "benchmark" if self.benchmark_path.exists() else "preview",
            "examples": self.preview["examples"],
            "rrf_k": ranking.RRF_K,
        }

    def search(self, query: str, mode: str, top_k: int) -> JSON:
        """Answer a search from the live index, or from the sample when a stub is unwritten."""
        conn = self._connect()
        try:
            if conn is not None:
                try:
                    with self._search_lock:
                        embedder = None if mode == "lexical" else self._embedder_for(conn)
                        response = search(conn, query, embedder=embedder, mode=mode, top_k=top_k)
                except (NotImplementedError, StubNotImplementedError):
                    pass  # a ranking stub is still empty: fall back to preview below
                else:
                    payload: JSON = dict(response.as_dict())
                    hits: list[JSON] = payload["hits"]  # type: ignore[assignment]
                    norms = db.embedding_norms(conn, [hit["chunk_id"] for hit in hits])
                    for hit in hits:
                        hit["file_path"] = _display_path(hit["file_path"])
                        hit["end_line"] = _end_line(hit["start_line"], hit["text"])
                        hit["tokens"] = len(tokenize(hit["text"]))
                        norm = norms.get(hit["chunk_id"])
                        hit["embedding_norm"] = None if norm is None else round(norm, 4)
                    payload.update(source="live", rrf_k=ranking.RRF_K)
                    return payload
            return self._preview_search(query, mode, top_k, self._preview_reason(conn))
        finally:
            if conn:
                conn.close()

    def _preview_search(self, query: str, mode: str, top_k: int, reason: str) -> JSON:
        key = _normalise(query)
        match = next((q for q in self.preview["searches"] if _normalise(q) == key), None)
        base: JSON = {
            "query": query,
            "mode": mode,
            "source": "preview",
            "reason": reason,
            "rrf_k": self.preview["rrf_k"],
            "model_load_ms": None,
        }
        if match is None:
            # Never invent hits for a query the preview did not record.
            return {**base, "hits": [], "timings_ms": {}, "preview_miss": True}
        recorded = self.preview["searches"][match][mode]
        return {**base, "hits": recorded["hits"][:top_k], "timings_ms": recorded["timings"]}

    def stats(self) -> JSON:
        """Index overview and latency over recent queries."""
        conn = self._connect()
        if conn is None:
            return {"source": "preview", **self.preview["stats"]}
        try:
            rows = db.recent_query_timings(conn, config.DEFAULT_STATS_WINDOW)
            latency: JSON = {}
            try:
                for stage in QUERY_STAGES:
                    summary = summarize(row[stage] for row in rows)
                    latency[stage] = None if summary is None else summary.as_dict()
            except NotImplementedError:
                # sift/telemetry.py:percentile is still a stub: serve the fixture.
                return {"source": "preview", **self.preview["stats"]}
            last = db.last_index_run(conn)
            return {
                "source": "live",
                "index": db.overview(conn),
                "last_index_run": None if last is None else dict(last),
                "query_latency": {"window": len(rows), "stages_ms": latency},
            }
        finally:
            conn.close()

    def document(self, path: str) -> JSON:
        """Full text of a file the index (or the preview) knows about; nothing else."""
        allowed: dict[str, Path] = {}
        for searches in self.preview["searches"].values():
            for recorded in searches.values():
                for hit in recorded["hits"]:
                    allowed[hit["file_path"]] = Path(hit["file_path"])
        conn = self._connect()
        source = "preview"
        if conn is not None:
            try:
                for (stored,) in conn.execute("SELECT path FROM files"):
                    allowed[_display_path(stored)] = Path(stored)
                    source = "live"
            finally:
                conn.close()
        target = allowed.get(path)
        if target is None:
            raise FileNotFoundError("not an indexed file")
        if target.stat().st_size > MAX_DOCUMENT_BYTES:
            raise FileNotFoundError("file too large to display")
        text = target.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "text": text, "source": source}

    def benchmark(self) -> JSON:
        """The latest benchmark results, or the bundled sample."""
        preview = {"generated_at": self.preview["generated_at"], **self.preview["benchmark"]}
        if not self.benchmark_path.exists():
            return {
                "source": "preview",
                "reason": "No benchmark results yet. Run `python eval/benchmark.py`.",
                **preview,
            }
        try:
            results = json.loads(self.benchmark_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "source": "preview",
                "reason": f"Could not read {self.benchmark_path}: {exc}",
                **preview,
            }
        mtime = datetime.fromtimestamp(self.benchmark_path.stat().st_mtime, timezone.utc)
        return {
            "source": "benchmark",
            "path": _display_path(str(self.benchmark_path)),
            "run_at": mtime.isoformat(timespec="seconds"),
            **results,
        }


def _normalise(query: str) -> str:
    return " ".join(query.casefold().split())


def _end_line(start_line: int, text: str) -> int:
    return start_line + text.rstrip("\n").count("\n")


def _display_path(path: str) -> str:
    try:
        relative = os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return path
    return path if relative.startswith("..") else relative


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


class Handler(BaseHTTPRequestHandler):
    """Serves the built UI and the read-only JSON API, ``no-store``, under a strict CSP."""
    server_version = f"sift-ui/{__version__}"
    inspector: Inspector  # bound by make_server

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        if os.environ.get("SIFT_UI_LOG"):
            super().log_message(format, *args)

    def _send(
        self, status: int, body: bytes, content_type: str, *, immutable: bool = False
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if immutable else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        """Route ``/api/*`` to the inspector and everything else to the static UI."""
        url = urlparse(self.path)
        try:
            if url.path.startswith("/api/"):
                self._api(url.path, parse_qs(url.query))
            else:
                self._static(url.path)
        except BrokenPipeError:
            pass
        except SiftError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
        except Exception as exc:  # a bug: report it, keep serving
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def _api(self, path: str, query: dict[str, list[str]]) -> None:
        inspector = self.inspector
        if path == "/api/status":
            return self._json(inspector.status())
        if path == "/api/stats":
            return self._json(inspector.stats())
        if path == "/api/benchmark":
            return self._json(inspector.benchmark())
        if path == "/api/document":
            target = (query.get("path") or [""])[0]
            try:
                return self._json(inspector.document(target))
            except (OSError, FileNotFoundError):
                return self._error(HTTPStatus.NOT_FOUND, "document not available")
        if path != "/api/search":
            return self._error(HTTPStatus.NOT_FOUND, f"no such endpoint: {path}")

        text = (query.get("q") or [""])[0].strip()
        mode = (query.get("mode") or [config.DEFAULT_MODE])[0]
        if not text:
            return self._error(HTTPStatus.BAD_REQUEST, "missing query parameter q")
        if len(text) > MAX_QUERY_CHARS:
            return self._error(HTTPStatus.BAD_REQUEST, "query too long")
        if mode not in config.SEARCH_MODES:
            return self._error(HTTPStatus.BAD_REQUEST, f"mode must be one of {config.SEARCH_MODES}")
        try:
            top_k = int((query.get("k") or [str(config.DEFAULT_TOP_K)])[0])
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "k must be an integer")
        if not 1 <= top_k <= MAX_TOP_K:
            return self._error(HTTPStatus.BAD_REQUEST, f"k must be within 1..{MAX_TOP_K}")
        started = time.perf_counter()
        payload = inspector.search(text, mode, top_k)
        payload["server_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return self._json(payload)

    def _static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if not target.is_relative_to(STATIC_DIR) or not target.is_file():
            if relative == "index.html":
                return self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "the UI is not built: run `npm install && npm run build` in web/",
                )
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".woff2":
            content_type = "font/woff2"
        elif content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        # Vite emits content-hashed files under assets/: safe to cache for a day.
        immutable = relative.startswith("assets/")
        self._send(HTTPStatus.OK, target.read_bytes(), content_type, immutable=immutable)


def make_server(host: str, port: int, inspector: Inspector) -> ThreadingHTTPServer:
    """Bind a threaded server to ``host:port`` that answers from ``inspector``."""
    handler = type("BoundHandler", (Handler,), {"inspector": inspector})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(db_path: Path, host: str, port: int, *, open_browser: bool) -> int:
    """Run the UI server until interrupted."""
    inspector = Inspector(db_path)
    try:
        server = make_server(host, port, inspector)
    except OSError as exc:
        raise SiftError(f"cannot listen on {host}:{port}: {exc.strerror or exc}") from exc
    url = f"http://{host}:{server.server_address[1]}/"
    status = inspector.status()
    print(f"sift inspector  {url}", flush=True)
    print(f"  index           {db_path}" + ("" if status["index"] else "  (not found)"))
    print(f"  search data     {status['search_source']}")
    print(f"  benchmark data  {status['benchmark_source']}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  warning: listening beyond localhost exposes your notes to the network")
    print("Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0
