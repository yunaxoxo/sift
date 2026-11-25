"""Tests for the web inspector API (``sift ui``).

The server runs on an ephemeral port in a background thread. Indexing uses
a one-chunk-per-file test double and the hash embedder, so nothing here needs a model or the
hand-written stubs; tests that need ranking skip themselves until it exists.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from helpers import SAMPLE_DOCS

from sift import db
from sift.embeddings import HashingEmbedder
from sift.indexer import index_directory
from sift.ui.server import Inspector, make_server


@pytest.fixture
def serve(tmp_path):
    servers = []

    def start(db_path: Path, benchmark_path: Path | None = None) -> str:
        inspector = Inspector(db_path, benchmark_path or tmp_path / "no-benchmark.json")
        server = make_server("127.0.0.1", 0, inspector)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def get(url: str) -> tuple[int, dict, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, dict(response.headers), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read() or b"{}")


def fetch_text(url: str) -> tuple[int, str, str]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.headers["Content-Type"], response.read().decode()


@pytest.fixture
def indexed_db(tmp_path) -> Path:
    path = tmp_path / "index.db"
    conn = db.connect(path, create=True)
    index_directory(conn, SAMPLE_DOCS, HashingEmbedder())
    conn.close()
    return path


class TestPreviewMode:
    def test_status_reports_preview_and_stub_state(self, serve, tmp_path):
        base = serve(tmp_path / "missing.db")
        status, _, body = get(f"{base}/api/status")
        assert status == 200
        assert body["index"] is None and body["search_source"] == "preview"
        assert set(body["stubs"]["ranking"]) == {
            "compute_bm25",
            "cosine_similarity",
            "reciprocal_rank_fusion",
        }
        assert len(body["examples"]) >= 3

    @pytest.mark.parametrize("mode", ["hybrid", "semantic", "lexical"])
    def test_example_queries_return_preview_hits(self, serve, tmp_path, mode):
        base = serve(tmp_path / "missing.db")
        example = get(f"{base}/api/status")[2]["examples"][0]
        query = urllib.parse.quote(example)
        _, _, body = get(f"{base}/api/search?q={query}&mode={mode}&k=3")
        assert body["source"] == "preview" and "sift index" in body["reason"]
        assert 1 <= len(body["hits"]) <= 3
        hit = body["hits"][0]
        assert hit["end_line"] >= hit["start_line"] >= 1
        assert {"lexical_rank", "semantic_rank", "score", "heading_context"} <= set(hit)

    def test_unknown_query_is_an_explicit_miss_not_fake_results(self, serve, tmp_path):
        base = serve(tmp_path / "missing.db")
        _, _, body = get(f"{base}/api/search?q=zebra+migration")
        assert body["source"] == "preview" and body["preview_miss"] and body["hits"] == []

    def test_benchmark_and_stats_fall_back_to_preview(self, serve, tmp_path):
        base = serve(tmp_path / "missing.db")
        _, _, bench = get(f"{base}/api/benchmark")
        assert bench["source"] == "preview"
        assert set(bench["modes"]) == {"hybrid", "semantic", "lexical"}
        _, _, stats = get(f"{base}/api/stats")
        assert stats["source"] == "preview"

    def test_existing_benchmark_results_are_served(self, serve, tmp_path):
        results = tmp_path / "latest.json"
        results.write_text(json.dumps({"environment": {"embedder": "hash:x"}, "modes": {}}))
        base = serve(tmp_path / "missing.db", results)
        _, _, bench = get(f"{base}/api/benchmark")
        assert bench["source"] == "benchmark" and bench["environment"]["embedder"] == "hash:x"


class TestLiveIndex:
    def test_status_and_stats_read_the_real_index(self, serve, indexed_db):
        base = serve(indexed_db)
        _, _, status = get(f"{base}/api/status")
        assert status["index"]["files"] == 12
        _, _, stats = get(f"{base}/api/stats")
        assert stats["source"] == "live" and stats["last_index_run"]["chunker"] == "rust"

    def test_search_is_live_once_ranking_exists(self, serve, indexed_db):
        base = serve(indexed_db)
        _, _, body = get(f"{base}/api/search?q=page+fault&mode=lexical&k=3")
        if body["source"] == "preview":
            assert "stub" in body["reason"]
            pytest.skip("sift/ranking.py is still a stub; the API fell back to preview")
        assert body["source"] == "live" and body["hits"]
        assert body["hits"][0]["file_path"].endswith(".md")


class TestHttp:
    @pytest.mark.parametrize(
        ("query", "status"),
        [
            ("", 400),
            ("?q=x&mode=fuzzy", 400),
            ("?q=x&k=0", 400),
            ("?q=x&k=999", 400),
            ("?q=x&k=five", 400),
        ],
    )
    def test_search_validates_parameters(self, serve, tmp_path, query, status):
        base = serve(tmp_path / "missing.db")
        assert get(f"{base}/api/search{query}")[0] == status

    def test_unknown_endpoint_is_404(self, serve, tmp_path):
        base = serve(tmp_path / "missing.db")
        assert get(f"{base}/api/nope")[0] == 404

    @pytest.mark.parametrize(
        "path",
        ["/../pyproject.toml", "/%2e%2e/%2e%2e/pyproject.toml", "/..%2fserver.py", "/missing.css"],
    )
    def test_static_files_cannot_escape_the_static_dir(self, serve, tmp_path, path):
        base = serve(tmp_path / "missing.db")
        assert get(f"{base}{path}")[0] == 404

    def test_index_and_assets_are_served_with_csp(self, serve, tmp_path):
        base = serve(tmp_path / "missing.db")
        status, content_type, html = fetch_text(f"{base}/")
        assert status == 200 and content_type.startswith("text/html") and 'id="root"' in html
        _, headers, _ = get(f"{base}/api/status")
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assets = re.findall(r'(?:src|href)="\./([^"]+)"', html)
        assert any(a.endswith(".js") for a in assets) and any(a.endswith(".css") for a in assets)
        assert any(a.endswith(".woff2") for a in assets), "first-screen fonts are preloaded"
        for asset in assets:
            # Fonts are binary, so check the status without decoding the body.
            with urllib.request.urlopen(f"{base}/{asset}", timeout=10) as response:
                assert response.status == 200

    def test_built_page_loads_nothing_from_other_hosts(self, serve, tmp_path):
        base = serve(tmp_path / "missing.db")
        html = fetch_text(f"{base}/")[2]
        assert not re.search(r"(?:src|href)=\"(?:https?:)?//", html)


class TestDocument:
    def test_serves_a_file_the_preview_knows_about(self, serve, tmp_path, monkeypatch):
        monkeypatch.chdir(SAMPLE_DOCS.parent)
        base = serve(tmp_path / "missing.db")
        query = urllib.parse.urlencode({"path": "sample_docs/os/virtual_memory.md"})
        status, _, body = get(f"{base}/api/document?{query}")
        assert status == 200 and body["text"].lstrip().startswith("#")

    @pytest.mark.parametrize("path", ["", "pyproject.toml", "../pyproject.toml", "/etc/passwd"])
    def test_refuses_anything_that_is_not_indexed(self, serve, tmp_path, path):
        base = serve(tmp_path / "missing.db")
        query = urllib.parse.urlencode({"path": path})
        assert get(f"{base}/api/document?{query}")[0] == 404

    def test_hits_carry_token_counts(self, serve, tmp_path):
        base = serve(tmp_path / "missing.db")
        example = get(f"{base}/api/status")[2]["examples"][0]
        _, _, body = get(f"{base}/api/search?{urllib.parse.urlencode({'q': example})}")
        assert body["hits"][0]["tokens"] > 0 and body["hits"][0]["embedding_norm"] > 0
