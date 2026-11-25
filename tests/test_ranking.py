"""Contract tests for the hand-written math in ``sift/ranking.py``.

Each test skips itself while the function it exercises still raises
``NotImplementedError``. The specification is IMPLEMENTATION_GUIDE.md.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from helpers import call_or_skip

from sift.ranking import (
    CorpusStats,
    compute_bm25,
    cosine_similarity,
    reciprocal_rank_fusion,
)

# N = 4 chunks, 5 tokens each on average; "page" is in 2 chunks, "fault" in 1.
STATS = CorpusStats(num_docs=4, avg_doc_len=5.0, doc_freqs={"page": 2, "fault": 1})
DOC = ["page", "table", "walk", "page"]


class TestComputeBm25:
    def test_worked_example_from_the_docstring(self):
        score = call_or_skip(compute_bm25, ["page", "fault"], DOC, STATS)
        assert score == pytest.approx(1.0098833, abs=1e-6)

    def test_returns_a_plain_float(self):
        score = call_or_skip(compute_bm25, ["page"], DOC, STATS)
        assert type(score) is float

    def test_every_matching_query_term_adds_to_the_score(self):
        score = call_or_skip(compute_bm25, ["page", "fault"], ["page", "fault", "handler"], STATS)
        assert score == pytest.approx(2.2682956, abs=1e-6)

    def test_repeated_query_term_counts_once(self):
        once = call_or_skip(compute_bm25, ["page", "fault"], DOC, STATS)
        twice = call_or_skip(compute_bm25, ["page", "page", "fault"], DOC, STATS)
        assert twice == pytest.approx(once)

    def test_no_matching_term_scores_zero(self):
        assert call_or_skip(compute_bm25, ["scheduler"], DOC, STATS) == 0.0

    @pytest.mark.parametrize(
        ("query", "doc", "stats"),
        [
            ([], DOC, STATS),
            (["page"], [], STATS),
            (["page"], DOC, CorpusStats(num_docs=0, avg_doc_len=0.0, doc_freqs={})),
        ],
        ids=["empty-query", "empty-document", "empty-corpus"],
    )
    def test_degenerate_inputs_score_zero_without_raising(self, query, doc, stats):
        assert call_or_skip(compute_bm25, query, doc, stats) == 0.0

    def test_term_missing_from_doc_freqs_is_treated_as_df_zero(self):
        score = call_or_skip(compute_bm25, ["walk"], DOC, STATS)
        assert score > 0.0 and math.isfinite(score)

    def test_idf_stays_positive_for_a_very_common_term(self):
        stats = CorpusStats(num_docs=10, avg_doc_len=4.0, doc_freqs={"the": 9})
        score = call_or_skip(compute_bm25, ["the"], ["the", "cat", "sat", "down"], stats)
        assert score == pytest.approx(0.1466035, abs=1e-6)

    def test_rare_term_outweighs_common_term(self):
        rare = call_or_skip(compute_bm25, ["fault"], ["fault", "x", "y", "z", "w"], STATS)
        common = call_or_skip(compute_bm25, ["page"], ["page", "x", "y", "z", "w"], STATS)
        assert rare > common

    def test_term_frequency_saturates(self):
        def score(tf: int) -> float:
            doc = ["page"] * tf + ["x"] * (10 - tf)
            return call_or_skip(compute_bm25, ["page"], doc, STATS)

        one, two, three = score(1), score(2), score(3)
        assert one < two < three
        assert (three - two) < (two - one)

    def test_longer_document_scores_lower_for_the_same_tf(self):
        short = call_or_skip(compute_bm25, ["page"], ["page", "x"], STATS)
        long = call_or_skip(compute_bm25, ["page"], ["page"] + ["x"] * 9, STATS)
        assert short > long

    def test_b_zero_disables_length_normalisation(self):
        short = call_or_skip(compute_bm25, ["page"], ["page", "x"], STATS, b=0.0)
        long = call_or_skip(compute_bm25, ["page"], ["page"] + ["x"] * 9, STATS, b=0.0)
        assert short == pytest.approx(long)
        assert call_or_skip(compute_bm25, ["page"], DOC, STATS, b=0.0) == pytest.approx(
            0.9530774, abs=1e-6
        )

    def test_k1_zero_reduces_to_idf(self):
        score = call_or_skip(compute_bm25, ["page"], DOC, STATS, k1=0.0)
        assert score == pytest.approx(math.log(2.0))


class TestCosineSimilarity:
    def test_worked_example_from_the_docstring(self):
        value = call_or_skip(cosine_similarity, [1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
        assert value == pytest.approx(0.9746318, abs=1e-6)

    def test_returns_a_plain_float_for_numpy_input(self):
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        value = call_or_skip(cosine_similarity, a, a)
        assert type(value) is float

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], 1.0),
            ([1.0, 0.0], [0.0, 1.0], 0.0),
            ([1.0, 2.0], [-1.0, -2.0], -1.0),
        ],
        ids=["identical", "orthogonal", "opposite"],
    )
    def test_reference_angles(self, a, b, expected):
        assert call_or_skip(cosine_similarity, a, b) == pytest.approx(expected, abs=1e-6)

    def test_ignores_vector_length(self):
        a, b = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
        scaled = [1000.0 * x for x in a]
        assert call_or_skip(cosine_similarity, scaled, b) == pytest.approx(
            call_or_skip(cosine_similarity, a, b), abs=1e-6
        )

    def test_is_symmetric(self):
        a, b = [0.3, -1.2, 4.0], [2.0, 0.5, -0.7]
        assert call_or_skip(cosine_similarity, a, b) == pytest.approx(
            call_or_skip(cosine_similarity, b, a)
        )

    def test_zero_vector_gives_zero_not_nan(self):
        value = call_or_skip(cosine_similarity, [0.0, 0.0, 0.0], [1.0, 2.0, 3.0])
        assert value == 0.0

    def test_dimension_mismatch_raises_value_error(self):
        call_or_skip(cosine_similarity, [1.0], [1.0])  # skip here if still a stub
        with pytest.raises(ValueError):
            cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0])

    def test_result_stays_within_bounds_despite_rounding(self):
        rng = np.random.default_rng(seed=7)
        for _ in range(200):
            v = rng.standard_normal(384).astype(np.float32)
            value = call_or_skip(cosine_similarity, v, v * np.float32(3.7))
            assert -1.0 <= value <= 1.0

    def test_does_not_modify_its_inputs(self):
        a = np.array([3.0, 4.0], dtype=np.float32)
        b = np.array([6.0, 8.0], dtype=np.float32)
        call_or_skip(cosine_similarity, a, b)
        assert a.tolist() == [3.0, 4.0] and b.tolist() == [6.0, 8.0]

    def test_accepts_read_only_arrays(self):
        a = np.frombuffer(np.array([1.0, 2.0], dtype="<f4").tobytes(), dtype="<f4")
        assert call_or_skip(cosine_similarity, a, a) == pytest.approx(1.0, abs=1e-6)


class TestReciprocalRankFusion:
    def test_worked_example_from_the_docstring(self):
        fused = call_or_skip(reciprocal_rank_fusion, [7, 3, 9], [3, 4, 7], 60)
        assert [chunk_id for chunk_id, _ in fused] == [3, 7, 4, 9]
        assert [score for _, score in fused] == pytest.approx(
            [1 / 62 + 1 / 61, 1 / 61 + 1 / 63, 1 / 62, 1 / 63]
        )

    def test_default_k_is_60(self):
        fused = call_or_skip(reciprocal_rank_fusion, [1], [])
        assert fused == [(1, pytest.approx(1 / 61))]

    def test_ties_are_broken_by_ascending_chunk_id(self):
        fused = call_or_skip(reciprocal_rank_fusion, [2, 1], [1, 2])
        assert [chunk_id for chunk_id, _ in fused] == [1, 2]
        assert fused[0][1] == pytest.approx(fused[1][1])

    def test_agreement_beats_a_single_top_rank(self):
        fused = call_or_skip(reciprocal_rank_fusion, [1, 5], [9, 5])
        assert fused[0][0] == 5

    def test_every_id_appears_exactly_once(self):
        fused = call_or_skip(reciprocal_rank_fusion, [1, 2, 3], [3, 4, 5])
        assert sorted(chunk_id for chunk_id, _ in fused) == [1, 2, 3, 4, 5]

    def test_scores_are_sorted_descending(self):
        fused = call_or_skip(reciprocal_rank_fusion, [4, 8, 15, 16], [23, 42, 8, 4])
        scores = [score for _, score in fused]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.parametrize(
        ("lexical", "semantic", "expected_ids"),
        [([], [], []), ([3, 1], [], [3, 1]), ([], [2, 9], [2, 9])],
        ids=["both-empty", "semantic-empty", "lexical-empty"],
    )
    def test_empty_inputs(self, lexical, semantic, expected_ids):
        fused = call_or_skip(reciprocal_rank_fusion, lexical, semantic)
        assert [chunk_id for chunk_id, _ in fused] == expected_ids

    def test_k_zero_is_plain_reciprocal_rank(self):
        fused = call_or_skip(reciprocal_rank_fusion, [1, 2], [2, 3], k=0)
        assert fused == [
            (2, pytest.approx(1.5)),
            (1, pytest.approx(1.0)),
            (3, pytest.approx(0.5)),
        ]

    def test_larger_k_shrinks_the_gap_between_ranks(self):
        def gap(k: int) -> float:
            fused = dict(call_or_skip(reciprocal_rank_fusion, [1, 2], [], k=k))
            return fused[1] / fused[2]

        assert gap(1) > gap(60) > 1.0

    def test_returns_a_list_of_int_float_tuples(self):
        fused = call_or_skip(reciprocal_rank_fusion, [1], [2])
        assert isinstance(fused, list)
        for item in fused:
            assert isinstance(item, tuple) and len(item) == 2
            assert isinstance(item[0], int) and isinstance(item[1], float)
