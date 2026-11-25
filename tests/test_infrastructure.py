"""Tests for the scaffolding: telemetry, storage,
tokenizer, embedder plumbing and the evaluation metrics. Tests of a stub skip
until it is written."""

from __future__ import annotations

import math
import sqlite3
import time

import ir_metrics
import numpy as np
import pytest
from helpers import call_or_skip

from sift import db
from sift.embeddings import HashingEmbedder, make_embedder
from sift.errors import IndexNotFoundError, SiftError
from sift.telemetry import StageTimer, percentile, summarize
from sift.tokenizer import tokenize

# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------


class TestPercentile:
    @pytest.mark.parametrize("q", [0, 10, 25, 50, 75, 90, 95, 99, 100])
    def test_matches_numpy_linear_interpolation(self, q):
        values = [12.5, 3.0, 7.25, 41.0, 0.5, 19.0, 8.0, 8.0, 100.0, 2.0, 33.3]
        assert call_or_skip(percentile, values, q) == pytest.approx(float(np.percentile(values, q)))

    def test_hand_computed_values(self):
        values = [10.0, 20.0, 30.0, 40.0]
        assert call_or_skip(percentile, values, 50) == 25.0
        assert call_or_skip(percentile, values, 95) == pytest.approx(38.5)

    def test_single_sample(self):
        assert call_or_skip(percentile, [7.0], 95) == 7.0

    def test_does_not_reorder_the_input(self):
        values = [3.0, 1.0, 2.0]
        call_or_skip(percentile, values, 50)
        assert values == [3.0, 1.0, 2.0]

    def test_rejects_empty_input_and_bad_q(self):
        with pytest.raises(ValueError):
            call_or_skip(percentile, [], 50)
        with pytest.raises(ValueError):
            call_or_skip(percentile, [1.0], 101)


class TestStageTimer:
    def test_measures_elapsed_time(self):
        timer = StageTimer()
        with timer.stage("work_ms"):
            time.sleep(0.01)
        assert 9.0 <= timer.get("work_ms") < 500.0

    def test_same_stage_accumulates(self):
        timer = StageTimer()
        timer.add("embed_ms", 2.0)
        timer.add("embed_ms", 3.5)
        assert timer.as_dict() == {"embed_ms": 5.5}

    def test_stage_is_recorded_even_when_the_body_raises(self):
        timer = StageTimer()
        with pytest.raises(RuntimeError), timer.stage("boom_ms"):
            raise RuntimeError
        assert timer.get("boom_ms") is not None

    def test_unknown_stage_is_none(self):
        assert StageTimer().get("never_ran") is None


class TestSummarize:
    def test_ignores_none_samples(self):
        summary = call_or_skip(summarize, [1.0, None, 3.0, None])
        assert summary.count == 2 and summary.mean == 2.0 and summary.max == 3.0

    def test_nothing_recorded_gives_none(self):
        assert summarize([None, None]) is None
        assert summarize([]) is None


# ---------------------------------------------------------------------------
# tokenizer
# ---------------------------------------------------------------------------


def test_tokenizer_lowercases_and_splits_on_punctuation():
    assert tokenize("Page-fault handling: the TLB & CR3!") == [
        "page",
        "fault",
        "handling",
        "the",
        "tlb",
        "cr3",
    ]


def test_tokenizer_keeps_unicode_words_and_underscores():
    assert tokenize("naïve Über_cool d_k") == ["naïve", "über_cool", "d_k"]
    assert tokenize("  \n ") == []


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "nested" / "index.db", create=True)
    yield connection
    connection.close()


class TestDatabase:
    def test_connect_without_create_refuses_missing_file(self, tmp_path):
        with pytest.raises(IndexNotFoundError):
            db.connect(tmp_path / "missing.db")

    def test_schema_and_pragmas(self, conn):
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"meta", "files", "chunks", "term_stats", "index_runs", "query_log"} <= tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_reopening_keeps_the_data(self, tmp_path):
        path = tmp_path / "index.db"
        first = db.connect(path, create=True)
        with first:
            db.set_meta(first, "embedding_model", "m")
        first.close()
        second = db.connect(path)
        assert db.get_meta(second, "embedding_model") == "m"
        second.close()

    def test_unknown_schema_version_is_rejected(self, tmp_path):
        path = tmp_path / "future.db"
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA user_version = 99")
        raw.close()
        with pytest.raises(SiftError, match="schema"):
            db.connect(path)

    def test_meta_upsert(self, conn):
        assert db.get_meta(conn, "k") is None
        db.set_meta(conn, "k", "1")
        db.set_meta(conn, "k", "2")
        assert db.get_meta(conn, "k") == "2"

    def test_vector_blob_round_trip(self):
        vector = np.array([0.25, -1.5, 3.0e-7, 1234.5], dtype=np.float32)
        blob = db.vector_to_blob(vector)
        assert len(blob) == 16
        assert np.array_equal(db.blob_to_vector(blob), vector)

    def _add_file(self, conn, path, chunk_tokens):
        file_id = db.insert_file(conn, path, "hash", len(chunk_tokens))
        db.insert_chunks(
            conn,
            [
                (file_id, i, 1, "H", "text", " ".join(t), len(t), db.vector_to_blob([1.0, 2.0]))
                for i, t in enumerate(chunk_tokens)
            ],
        )
        return file_id

    def test_term_stats_count_chunks_not_occurrences(self, conn):
        with conn:
            self._add_file(conn, "/r/a.md", [["page", "page", "fault"], ["page"]])
            self._add_file(conn, "/r/b.md", [["tlb"]])
            assert db.rebuild_term_stats(conn) == 3
        assert db.doc_freqs(conn, ["page", "fault", "tlb", "absent"]) == {
            "page": 2,
            "fault": 1,
            "tlb": 1,
        }
        assert db.corpus_size(conn) == (3, pytest.approx(5 / 3))

    def test_deleting_a_file_cascades_to_its_chunks(self, conn):
        with conn:
            file_id = self._add_file(conn, "/r/a.md", [["x"], ["y"]])
            self._add_file(conn, "/r/b.md", [["z"]])
            db.delete_files(conn, [file_id])
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1

    def test_indexed_files_under_matches_whole_path_components(self, conn):
        with conn:
            self._add_file(conn, "/r/notes/a.md", [["x"]])
            self._add_file(conn, "/r/notes-old/b.md", [["y"]])
            self._add_file(conn, "/r/100%_real/c.md", [["z"]])
        assert set(db.indexed_files_under(conn, "/r/notes")) == {"/r/notes/a.md"}
        assert set(db.indexed_files_under(conn, "/r/100%_real")) == {"/r/100%_real/c.md"}

    def test_chunk_iterators_and_lookup(self, conn):
        with conn:
            self._add_file(conn, "/r/a.md", [["x", "y"], ["z"]])
        tokens = list(db.iter_chunk_tokens(conn))
        assert [t for _, t in tokens] == [["x", "y"], ["z"]]
        vectors = list(db.iter_chunk_embeddings(conn))
        assert vectors[0][1].tolist() == [1.0, 2.0]
        rows = db.chunks_by_id(conn, [tokens[1][0]])
        assert rows[tokens[1][0]]["file_path"] == "/r/a.md"
        assert rows[tokens[1][0]]["chunk_index"] == 1

    def test_query_log_feeds_recent_timings(self, conn):
        db.record_query(
            conn,
            query="q1",
            mode="lexical",
            top_k=5,
            n_results=2,
            timings={"lexical_ms": 1.5, "fusion_ms": 0.1, "total_ms": 2.0},
        )
        row = db.recent_query_timings(conn, 10)[0]
        assert row["embed_ms"] is None and row["lexical_ms"] == 1.5 and row["total_ms"] == 2.0

    def test_query_log_is_trimmed(self, conn, monkeypatch):
        monkeypatch.setattr(db, "QUERY_LOG_MAX_ROWS", 3)
        for n in range(6):
            db.record_query(
                conn, query=f"q{n}", mode="lexical", top_k=1, n_results=0, timings={"total_ms": 1.0}
            )
        kept = [row[0] for row in conn.execute("SELECT query FROM query_log ORDER BY id")]
        assert kept == ["q3", "q4", "q5"]


# ---------------------------------------------------------------------------
# embedders
# ---------------------------------------------------------------------------


class TestHashingEmbedder:
    def test_shape_dtype_and_unit_length(self):
        embedder = HashingEmbedder(64)
        vectors = embedder.encode(["page fault handler", "tcp slow start", ""])
        assert vectors.shape == (3, 64) and vectors.dtype == np.float32
        assert np.linalg.norm(vectors[0]) == pytest.approx(1.0, abs=1e-5)
        assert not vectors[2].any()

    def test_is_deterministic(self):
        a = HashingEmbedder(64).encode(["the same text"])
        b = HashingEmbedder(64).encode(["the same text"])
        assert np.array_equal(a, b)

    def test_make_embedder_restores_dimension_from_stored_name(self):
        embedder = make_embedder("hash", "feature-hash-32")
        assert embedder.dim == 32 and embedder.model == "feature-hash-32"

    def test_real_embedders_are_lazy(self):
        # Constructing must not import torch or touch the network.
        embedder = make_embedder("sentence-transformers", "some/model")
        assert embedder.load_ms is None and embedder.model == "some/model"

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(SiftError):
            make_embedder("word2vec")


# ---------------------------------------------------------------------------
# evaluation metrics (eval/ir_metrics.py)
# ---------------------------------------------------------------------------


class TestIrMetrics:
    truth = {"a.md": 2, "b.md": 1}

    def test_recall_at_k(self):
        assert call_or_skip(ir_metrics.recall_at_k, ["a.md", "x.md", "b.md"], self.truth, 3) == 1.0
        assert call_or_skip(ir_metrics.recall_at_k, ["a.md", "x.md", "b.md"], self.truth, 2) == 0.5
        assert call_or_skip(ir_metrics.recall_at_k, ["x.md", "y.md"], self.truth, 3) == 0.0

    def test_reciprocal_rank(self):
        assert call_or_skip(ir_metrics.reciprocal_rank, ["x.md", "b.md", "a.md"], self.truth) == 0.5
        assert call_or_skip(ir_metrics.reciprocal_rank, ["a.md"], self.truth) == 1.0
        assert call_or_skip(ir_metrics.reciprocal_rank, ["x.md"], self.truth) == 0.0

    def test_ndcg_perfect_and_empty(self):
        perfect = call_or_skip(ir_metrics.ndcg_at_k, ["a.md", "b.md", "x.md"], self.truth, 5)
        assert perfect == pytest.approx(1.0)
        assert call_or_skip(ir_metrics.ndcg_at_k, ["x.md", "y.md"], self.truth, 5) == 0.0

    def test_ndcg_hand_computed(self):
        ideal = 3.0 + 1.0 / math.log2(3)
        swapped = call_or_skip(ir_metrics.ndcg_at_k, ["b.md", "a.md"], self.truth, 5)
        assert swapped == pytest.approx((1.0 + 3.0 / math.log2(3)) / ideal)
        late = call_or_skip(ir_metrics.ndcg_at_k, ["x.md", "y.md", "a.md"], self.truth, 5)
        assert late == pytest.approx(1.5 / ideal)

    def test_ndcg_respects_the_cutoff(self):
        ranked = ["x.md", "y.md", "z.md", "a.md"]
        assert call_or_skip(ir_metrics.ndcg_at_k, ranked, self.truth, 3) == 0.0
        assert call_or_skip(ir_metrics.ndcg_at_k, ranked, self.truth, 4) > 0.0

    def test_bad_inputs_raise(self):
        with pytest.raises(ValueError):
            call_or_skip(ir_metrics.recall_at_k, ["a.md"], {}, 3)
        with pytest.raises(ValueError):
            call_or_skip(ir_metrics.reciprocal_rank, ["a.md", "a.md"], self.truth)
        with pytest.raises(ValueError):
            call_or_skip(ir_metrics.mean, [])
