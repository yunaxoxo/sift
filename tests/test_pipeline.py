"""End-to-end tests of the index and search pipelines, the CLI and the benchmark.

Indexing uses a one-chunk-per-file test double (tests/conftest.py) and the hash
embedder, so these run in milliseconds with no model download and do not depend
on the Rust stubs.
Anything that needs ``sift/ranking.py`` skips itself until it is implemented.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import benchmark
import pytest
from helpers import SAMPLE_DOCS, call_or_skip

from sift import db
from sift.cli import main
from sift.embeddings import HashingEmbedder
from sift.errors import EXIT_ERROR, EXIT_OK, EXIT_STUB, SiftError, StubNotImplementedError
from sift.indexer import index_directory, searchable_text
from sift.search import search


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    """A private three-file copy of the corpus that tests are free to modify."""
    root = tmp_path / "notes"
    for relative in [
        "os/virtual_memory.md",
        "networking/dns_resolution.md",
        "ml/transformer_attention.md",
    ]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SAMPLE_DOCS / relative, target)
    return root


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "index.db", create=True)
    yield connection
    connection.close()


def index(conn, root, **kwargs):
    return index_directory(conn, root, kwargs.pop("embedder", HashingEmbedder()), **kwargs)


# ---------------------------------------------------------------------------
# indexing
# ---------------------------------------------------------------------------


class TestIndexing:
    def test_first_run_indexes_everything(self, conn, notes):
        report = index(conn, notes)
        assert (report.files_seen, report.files_indexed, report.files_unchanged) == (3, 3, 0)
        assert report.chunks_embedded == 3
        assert {"chunk_ms", "embed_ms", "db_write_ms", "total_ms"} <= set(report.timings)
        overview = db.overview(conn)
        assert overview["files"] == 3 and overview["chunks"] == 3
        assert overview["embedder"] == "hash" and overview["embedding_dim"] == 256
        assert overview["vocabulary"] > 100

    def test_second_run_is_a_no_op_and_never_loads_the_model(self, conn, notes):
        index(conn, notes)
        fresh = HashingEmbedder()
        report = index(conn, notes, embedder=fresh)
        assert (report.files_indexed, report.files_unchanged) == (0, 3)
        assert report.chunks_embedded == 0
        assert fresh.load_ms is None

    def test_only_the_edited_file_is_re_embedded(self, conn, notes):
        index(conn, notes)
        edited = notes / "os" / "virtual_memory.md"
        edited.write_text(edited.read_text() + "\n\nA brand new paragraph about zswap.\n")
        report = index(conn, notes)
        assert (report.files_indexed, report.files_unchanged) == (1, 2)
        assert db.doc_freqs(conn, ["zswap"]) == {"zswap": 1}
        assert db.overview(conn)["chunks"] == 3

    def test_deleted_files_leave_the_index(self, conn, notes):
        index(conn, notes)
        (notes / "networking" / "dns_resolution.md").unlink()
        report = index(conn, notes)
        assert report.files_removed == 1
        assert db.overview(conn)["files"] == 2
        assert db.doc_freqs(conn, ["nxdomain"]) == {}

    @pytest.mark.native_chunker
    def test_hidden_and_non_matching_files_are_ignored(self, conn, notes):
        (notes / ".obsidian").mkdir()
        (notes / ".obsidian" / "cache.md").write_text("# hidden\n")
        (notes / "todo.txt").write_text("not markdown\n")
        (notes / "empty.md").write_text("   \n")
        try:
            assert index(conn, notes).files_seen == 3
        except StubNotImplementedError:
            pytest.skip("Rust walker is still todo!()")

    def test_changing_the_embedder_requires_rebuild(self, conn, notes):
        index(conn, notes)
        with pytest.raises(SiftError, match="--rebuild"):
            index(conn, notes, embedder=HashingEmbedder(64))
        report = index(conn, notes, embedder=HashingEmbedder(64), rebuild=True)
        assert report.files_indexed == 3
        assert db.overview(conn)["embedding_dim"] == 64

    def test_every_run_is_recorded(self, conn, notes):
        index(conn, notes)
        index(conn, notes)
        run = db.last_index_run(conn)
        assert run["chunker"] == "rust" and run["files_unchanged"] == 3
        assert conn.execute("SELECT COUNT(*) FROM index_runs").fetchone()[0] == 2

    @pytest.mark.native_chunker
    def test_rust_chunker_reports_a_stub_or_works(self, conn, notes):
        try:
            report = index(conn, notes, chunker="rust")
        except StubNotImplementedError:
            pytest.skip("Rust chunker is still todo!()")
        assert report.files_seen == 3 and report.chunks_seen >= 3

    def test_heading_context_is_prepended_to_searchable_text(self):
        class Fake:
            heading_context = "TCP > Slow start"
            text = "The window doubles every round trip."

        assert searchable_text(Fake()).startswith("TCP > Slow start\n")
        Fake.heading_context = ""
        assert searchable_text(Fake()) == Fake.text


# ---------------------------------------------------------------------------
# search (needs sift/ranking.py)
# ---------------------------------------------------------------------------


class TestSearch:
    def test_lexical_mode_finds_the_obvious_note(self, conn, notes):
        index(conn, notes)
        response = call_or_skip(search, conn, "TLB shootdown", embedder=None, mode="lexical")
        assert Path(response.hits[0].file_path).name == "virtual_memory.md"
        assert response.hits[0].lexical_rank == 1 and response.hits[0].semantic_rank is None
        assert "embed_ms" not in response.timings and "lexical_ms" in response.timings

    def test_hybrid_mode_reports_every_stage_and_both_ranks(self, conn, notes):
        index(conn, notes)
        response = call_or_skip(
            search, conn, "recursive resolver cache", embedder=HashingEmbedder(), top_k=2
        )
        assert len(response.hits) == 2
        assert [hit.rank for hit in response.hits] == [1, 2]
        assert set(response.timings) == {
            "embed_ms",
            "lexical_ms",
            "vector_ms",
            "fusion_ms",
            "total_ms",
        }
        assert Path(response.hits[0].file_path).name == "dns_resolution.md"
        assert response.hits[0].semantic_rank is not None

    def test_queries_are_logged_unless_disabled(self, conn, notes):
        index(conn, notes)
        call_or_skip(search, conn, "page fault", embedder=None, mode="lexical")
        search(conn, "page fault", embedder=None, mode="lexical", log=False)
        assert conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0] == 1

    def test_query_with_no_matching_terms_returns_nothing_in_lexical_mode(self, conn, notes):
        index(conn, notes)
        response = call_or_skip(search, conn, "zzzqqq", embedder=None, mode="lexical")
        assert response.hits == []

    def test_argument_validation_does_not_need_the_stubs(self, conn, notes):
        index(conn, notes)
        with pytest.raises(SiftError):
            search(conn, "q", embedder=None, mode="hybrid")
        with pytest.raises(SiftError):
            search(conn, "q", embedder=None, mode="fuzzy")
        with pytest.raises(SiftError, match="dimensional"):
            search(conn, "q", embedder=HashingEmbedder(8), mode="semantic")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def run(self, tmp_path, *argv):
        return main(["--db", str(tmp_path / "cli.db"), *argv])

    def test_index_then_stats(self, tmp_path, notes, capsys):
        code = self.run(tmp_path, "index", str(notes), "--embedder", "hash")
        assert code == EXIT_OK
        assert "3 seen · 3 indexed" in capsys.readouterr().out

        assert self.run(tmp_path, "stats", "--json") == EXIT_OK
        stats = json.loads(capsys.readouterr().out)
        assert stats["index"]["files"] == 3
        assert stats["last_index_run"]["chunker"] == "rust"
        assert stats["query_latency"]["window"] == 0

    def test_search_exits_0_or_3_never_a_traceback(self, tmp_path, notes, capsys):
        self.run(tmp_path, "index", str(notes), "--embedder", "hash")
        capsys.readouterr()
        code = self.run(tmp_path, "search", "page fault", "--mode", "lexical", "--json")
        captured = capsys.readouterr()
        if code == EXIT_STUB:
            assert "not implemented yet" in captured.err
            pytest.skip("sift/ranking.py is still a stub")
        assert code == EXIT_OK
        assert json.loads(captured.out)["hits"][0]["file_path"].endswith("virtual_memory.md")

    @pytest.mark.native_chunker
    def test_rust_chunker_exits_0_or_3(self, tmp_path, notes, capsys):
        code = self.run(tmp_path, "index", str(notes), "--embedder", "hash")
        assert code in (EXIT_OK, EXIT_STUB)
        if code == EXIT_STUB:
            assert "todo!()" in capsys.readouterr().err

    def test_missing_index_is_a_clean_error(self, tmp_path, capsys):
        assert self.run(tmp_path, "stats") == EXIT_ERROR
        assert "sift index" in capsys.readouterr().err

    def test_bad_directory_is_a_clean_error(self, tmp_path, capsys):
        assert self.run(tmp_path, "index", str(tmp_path / "nope")) == EXIT_ERROR
        assert "not a directory" in capsys.readouterr().err

    def test_db_flag_is_accepted_after_the_subcommand(self, tmp_path, notes):
        db_path = tmp_path / "late.db"
        argv = ["index", str(notes), "--embedder", "hash"]
        assert main([*argv, "--db", str(db_path)]) == EXIT_OK
        assert db_path.exists()


# ---------------------------------------------------------------------------
# benchmark harness (eval/benchmark.py)
# ---------------------------------------------------------------------------


class TestBenchmark:
    def test_ground_truth_is_well_formed(self):
        queries = benchmark.load_queries(benchmark.EVAL_DIR / "queries.json")
        corpus = {path.name for path in SAMPLE_DOCS.rglob("*.md")}
        assert len(queries) == 20 and len(corpus) == 12
        benchmark.check_ground_truth(queries, corpus)
        primaries = {name for q in queries for name, grade in q["relevant"].items() if grade == 2}
        assert primaries == corpus, "every note should be the best answer to some query"

    def test_ground_truth_check_catches_a_renamed_note(self):
        queries = [{"id": "q", "query": "x", "relevant": {"gone.md": 2}}]
        with pytest.raises(SiftError, match="gone.md"):
            benchmark.check_ground_truth(queries, {"here.md"})

    def test_chunk_hits_collapse_to_a_file_ranking(self):
        class Hit:
            def __init__(self, path):
                self.file_path = path

        hits = [Hit("/r/a.md"), Hit("/r/b.md"), Hit("/r/a.md"), Hit("/r/c.md")]
        assert benchmark.ranked_files(hits) == ["a.md", "b.md", "c.md"]

    def _results(self, recall, p95=5.0):
        quality = {"recall@3": recall, "ndcg@5": 0.9, "mrr": 0.9}
        latency = {"total_ms": {"count": 1, "mean": 1.0, "p50": 1.0, "p95": p95, "max": p95}}
        return {
            "profile": "hash:x|rust",
            "modes": {"hybrid": {"quality": quality, "latency_ms": latency}},
        }

    def test_baseline_gate(self, tmp_path):
        path = tmp_path / "baseline.json"
        assert (
            "no baseline"
            in benchmark.check_against_baseline(path, self._results(0.9), 0.02, None)[0]
        )

        benchmark.update_baseline(path, self._results(0.9))
        assert benchmark.check_against_baseline(path, self._results(0.9), 0.02, None) == []
        assert benchmark.check_against_baseline(path, self._results(0.885), 0.02, None) == []
        assert benchmark.check_against_baseline(path, self._results(0.95), 0.02, None) == []

        regressions = benchmark.check_against_baseline(path, self._results(0.8), 0.02, None)
        assert len(regressions) == 1 and "recall@3" in regressions[0]

        slow = benchmark.check_against_baseline(path, self._results(0.9, p95=80.0), 0.02, 50.0)
        assert len(slow) == 1 and "p95" in slow[0]

    def test_full_run_exits_0_or_3(self, tmp_path, capsys):
        code = benchmark.main(
            [
                "--embedder", "hash",
                "--repeats", "1",
                "--output", str(tmp_path / "results.json"),
                "--baseline", str(tmp_path / "baseline.json"),
                "--update-baseline",
            ]
        )  # fmt: skip
        captured = capsys.readouterr()
        if code == EXIT_STUB:
            assert "not implemented yet" in captured.err
            pytest.skip("sift/ranking.py is still a stub")
        assert code == EXIT_OK
        assert "| hybrid |" in captured.out
        results = json.loads((tmp_path / "results.json").read_text())
        assert set(results["modes"]) == {"hybrid", "lexical", "semantic"}
        assert results["corpus"]["files"] == 12
        assert (tmp_path / "baseline.json").exists()
