"""Contract tests for the Rust extension ``sift._rust_core``.

These go through the real FFI boundary, so they check your algorithms *and* the
PyO3 conversions at once. Contract ids (W1.., C1.., F1..) are those of
IMPLEMENTATION_GUIDE.md sections 7 and 8.

* ``TestPyO3Surface`` passes on the untouched scaffold (it tests ``lib.rs``).
* Everything else skips itself until the matching ``todo!()`` is gone.

Rebuild after every Rust change, or you are testing the old binary::

    maturin develop && pytest tests/test_rust_contract.py -ra
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from helpers import SAMPLE_DOCS, call_or_skip

core = pytest.importorskip(
    "sift._rust_core", reason="Rust extension not built; run `maturin develop`"
)


def chunk(text: str, *, max_chars: int = 200, overlap: int = 0, path: str = "notes.md"):
    return call_or_skip(core.chunk_text, path, text, max_chars, overlap)


def chunk_containing(chunks, needle: str):
    matches = [c for c in chunks if needle in c.text]
    assert matches, f"no chunk contains {needle!r}"
    return matches[0]


OUTLINE = """\
intro before any heading

# Alpha

alpha body

## Beta

beta body

### Gamma

gamma body

## Delta

delta body

# Epsilon

epsilon body
"""

FENCES = """\
# Setup

```bash
# install deps
pip install example
```

between the fences

~~~
# also not a heading
~~~

## Real

real body
"""

LONG_SECTION = "# Big\n\n" + " ".join(
    f"Sentence number {n} talks about paging and faults." for n in range(40)
)


# ---------------------------------------------------------------------------
# lib.rs: already implemented, must pass today
# ---------------------------------------------------------------------------


class TestPyO3Surface:
    def test_module_metadata(self):
        assert isinstance(core.__version__, str)
        assert core.DEFAULT_MAX_CHARS == 1200
        assert core.DEFAULT_OVERLAP == 150
        assert core.DEFAULT_EXTENSIONS == ["md", "markdown"]

    def test_document_chunk_round_trips_its_fields(self):
        c = core.DocumentChunk("a.md", 3, "héllo", 7, "A > B")
        assert (c.file_path, c.chunk_index, c.text, c.start_line, c.heading_context) == (
            "a.md",
            3,
            "héllo",
            7,
            "A > B",
        )

    def test_len_counts_characters_not_bytes(self):
        assert len(core.DocumentChunk("a.md", 0, "naïve", 1, "")) == 5

    def test_document_chunk_is_immutable(self):
        c = core.DocumentChunk("a.md", 0, "text", 1, "")
        with pytest.raises(AttributeError):
            c.text = "changed"

    def test_document_chunk_equality_and_repr(self):
        a = core.DocumentChunk("a.md", 0, "text", 1, "H")
        assert a == core.DocumentChunk("a.md", 0, "text", 1, "H")
        assert a != core.DocumentChunk("a.md", 1, "text", 1, "H")
        assert "a.md" in repr(a) and "chunk_index=0" in repr(a)

    def test_type_reports_its_real_module(self):
        assert core.DocumentChunk.__module__ == "sift._rust_core"

    @pytest.mark.parametrize(("max_chars", "overlap"), [(0, 0), (100, 100), (100, 500)])
    def test_bad_chunk_sizes_raise_value_error(self, max_chars, overlap):
        with pytest.raises(ValueError):
            core.chunk_text("a.md", "text", max_chars, overlap)

    def test_negative_size_is_rejected(self):
        with pytest.raises(OverflowError):
            core.chunk_text("a.md", "text", -1, 0)

    def test_missing_root_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            core.walk_files(str(tmp_path / "nope"))
        with pytest.raises(FileNotFoundError):
            core.chunk_directory(str(tmp_path / "nope"))

    def test_root_that_is_a_file_raises_not_a_directory(self, tmp_path):
        target = tmp_path / "file.md"
        target.write_text("x")
        with pytest.raises(NotADirectoryError):
            core.walk_files(str(target))

    def test_wrong_argument_type_raises_type_error(self):
        with pytest.raises(TypeError):
            core.chunk_text("a.md", 12345)


# ---------------------------------------------------------------------------
# walker.rs
# ---------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for relative in [
        "a.md",
        "B.MD",
        "notes.txt",
        "README",
        "sub/deep/c.md",
        "sub/e.markdown",
        ".hidden/d.md",
        ".secret.md",
    ]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n\nbody\n", encoding="utf-8")
    return root


class TestWalkFiles:
    def test_w1_w2_w3_w6_recursive_filtered_sorted(self, tree):
        found = call_or_skip(core.walk_files, str(tree))
        expected = [str(tree / p) for p in ["B.MD", "a.md", "sub/deep/c.md", "sub/e.markdown"]]
        assert found == expected

    def test_w2_explicit_extensions(self, tree):
        assert call_or_skip(core.walk_files, str(tree), ["txt"]) == [str(tree / "notes.txt")]
        assert call_or_skip(core.walk_files, str(tree), [".TXT"]) == [str(tree / "notes.txt")]

    def test_w2_files_without_extension_never_match(self, tree):
        found = call_or_skip(core.walk_files, str(tree), ["md", "readme", ""])
        assert not any(path.endswith("README") for path in found)

    def test_w3_hidden_entries_are_skipped(self, tree):
        found = call_or_skip(core.walk_files, str(tree))
        assert not any(".hidden" in path or ".secret" in path for path in found)

    def test_w4_symlinks_are_not_followed(self, tree, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "z.md").write_text("# outside\n")
        try:
            os.symlink(outside, tree / "link", target_is_directory=True)
        except OSError:
            pytest.skip("cannot create symlinks on this platform")
        found = call_or_skip(core.walk_files, str(tree))
        assert not any(path.endswith("z.md") for path in found)

    def test_w5_paths_are_root_joined_not_canonicalised(self, tree):
        found = call_or_skip(core.walk_files, str(tree))
        assert found and all(path.startswith(str(tree) + os.sep) for path in found)

    def test_w6_order_is_stable_across_runs(self, tree):
        first = call_or_skip(core.walk_files, str(tree))
        assert all(call_or_skip(core.walk_files, str(tree)) == first for _ in range(5))

    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert call_or_skip(core.walk_files, str(tmp_path)) == []


# ---------------------------------------------------------------------------
# chunker.rs: chunk_document (through chunk_text)
# ---------------------------------------------------------------------------


class TestChunkText:
    @pytest.mark.parametrize("text", ["", "   ", "\n\n\t\n"], ids=["empty", "spaces", "blank"])
    def test_c1_blank_input_gives_no_chunks(self, text):
        assert chunk(text) == []

    def test_c1_no_chunk_is_blank(self):
        for text in (OUTLINE, FENCES, LONG_SECTION):
            assert all(c.text.strip() for c in chunk(text, max_chars=120))

    def test_c2_chunk_index_is_sequential(self):
        chunks = chunk(LONG_SECTION, max_chars=120)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_file_path_is_copied_to_every_chunk(self):
        assert {c.file_path for c in chunk(OUTLINE, path="dir/notes.md")} == {"dir/notes.md"}

    @pytest.mark.parametrize("overlap", [0, 30])
    def test_c3_size_cap_holds_including_overlap(self, overlap):
        chunks = chunk(LONG_SECTION, max_chars=120, overlap=overlap)
        assert len(chunks) > 1
        assert max(len(c.text) for c in chunks) <= 120

    def test_c3_multibyte_text_is_cut_on_char_boundaries(self):
        text = "naïve café – 日本語のテキスト 🚀 Ünïcödé ≤ ∞ " * 30
        chunks = chunk(text, max_chars=50)
        assert len(chunks) > 1
        assert max(len(c.text) for c in chunks) <= 50
        joined = " ".join(c.text for c in chunks)
        assert all(word in joined for word in set(text.split()))

    def test_c4_heading_opens_a_chunk_and_is_part_of_its_text(self):
        chunks = chunk(OUTLINE)
        assert chunk_containing(chunks, "beta body").text.lstrip().startswith("## Beta")
        assert chunk_containing(chunks, "epsilon body").text.lstrip().startswith("# Epsilon")

    def test_c4_sections_are_never_merged_across_headings(self):
        chunks = chunk(OUTLINE, max_chars=1000)
        bodies = ["alpha body", "beta body", "gamma body", "delta body", "epsilon body"]
        for c in chunks:
            assert sum(body in c.text for body in bodies) <= 1

    def test_c4_only_real_atx_headings_count(self):
        text = "# Title\n\n#hashtag without a space\n\n####### seven hashes\n\nbody text\n"
        assert {c.heading_context for c in chunk(text)} == {"Title"}

    def test_c5_breadcrumb_tracks_the_heading_stack(self):
        chunks = chunk(OUTLINE)
        expected = {
            "intro before": "",
            "alpha body": "Alpha",
            "beta body": "Alpha > Beta",
            "gamma body": "Alpha > Beta > Gamma",
            "delta body": "Alpha > Delta",
            "epsilon body": "Epsilon",
        }
        for needle, breadcrumb in expected.items():
            assert chunk_containing(chunks, needle).heading_context == breadcrumb, needle

    def test_c5_every_piece_of_a_split_section_shares_the_breadcrumb(self):
        chunks = chunk(LONG_SECTION, max_chars=120)
        assert len(chunks) > 1
        assert {c.heading_context for c in chunks} == {"Big"}

    def test_c6_hash_lines_inside_code_fences_are_not_headings(self):
        chunks = chunk(FENCES)
        contexts = {c.heading_context for c in chunks}
        assert not any("install deps" in ctx or "also not" in ctx for ctx in contexts)
        assert chunk_containing(chunks, "pip install example").heading_context == "Setup"
        assert chunk_containing(chunks, "between the fences").heading_context == "Setup"
        assert chunk_containing(chunks, "real body").heading_context == "Setup > Real"

    def test_c7_oversized_section_is_split(self):
        assert len(chunk(LONG_SECTION, max_chars=5000)) == 1
        assert len(chunk(LONG_SECTION, max_chars=120)) > 5

    def test_c8_zero_overlap_means_no_duplicated_text(self):
        chunks = chunk(LONG_SECTION, max_chars=120, overlap=0)
        assert sum(len(c.text) for c in chunks) <= len(LONG_SECTION)

    def test_c8_overlap_repeats_text_between_neighbours(self):
        plain = chunk(LONG_SECTION, max_chars=120, overlap=0)
        overlapped = chunk(LONG_SECTION, max_chars=120, overlap=40)
        assert sum(len(c.text) for c in overlapped) > sum(len(c.text) for c in plain)

    def test_c9_start_line_points_at_the_chunk_s_first_line(self):
        lines = OUTLINE.splitlines()
        chunks = chunk(OUTLINE)
        for c in chunks:
            assert 1 <= c.start_line <= len(lines)
            assert c.text.splitlines()[0].strip() in lines[c.start_line - 1]
        gamma = chunk_containing(chunks, "gamma body")
        assert gamma.start_line == lines.index("### Gamma") + 1

    def test_c9_start_line_never_decreases(self):
        for text in (OUTLINE, FENCES, LONG_SECTION):
            starts = [c.start_line for c in chunk(text, max_chars=120, overlap=20)]
            assert starts == sorted(starts)

    def test_c10_no_content_is_lost(self):
        for text in (OUTLINE, FENCES, LONG_SECTION):
            joined = " ".join(c.text for c in chunk(text, max_chars=120))
            missing = [word for word in set(text.split()) if word not in joined]
            assert not missing

    def test_c11_non_markdown_files_have_no_headings(self):
        source = "# a comment, not a heading\nx = 1\n\n# another comment\ny = 2\n"
        chunks = chunk(source, path="script.py")
        assert chunks and {c.heading_context for c in chunks} == {""}

    @pytest.mark.parametrize("name", ["NOTES.MD", "a.markdown", "page.mdx"])
    def test_c11_markdown_extensions_are_case_insensitive(self, name):
        chunks = chunk("# Title\n\nbody\n", path=name)
        assert chunk_containing(chunks, "body").heading_context == "Title"


# ---------------------------------------------------------------------------
# chunker.rs: chunk_files (through chunk_directory)
# ---------------------------------------------------------------------------


class TestChunkDirectory:
    def test_f3_sample_docs_grouped_sorted_and_indexed(self):
        chunks = call_or_skip(core.chunk_directory, str(SAMPLE_DOCS))
        files = list(dict.fromkeys(c.file_path for c in chunks))
        assert len(files) == 12
        assert files == call_or_skip(core.walk_files, str(SAMPLE_DOCS))
        by_file: dict[str, list[int]] = {}
        previous = None
        for c in chunks:
            if c.file_path != previous:
                assert c.file_path not in by_file, "a file's chunks must be contiguous"
            by_file.setdefault(c.file_path, []).append(c.chunk_index)
            previous = c.file_path
        for indexes in by_file.values():
            assert indexes == list(range(len(indexes)))

    def test_f3_output_is_deterministic(self):
        first = call_or_skip(core.chunk_directory, str(SAMPLE_DOCS))
        for _ in range(3):
            assert call_or_skip(core.chunk_directory, str(SAMPLE_DOCS)) == first

    def test_f1_matches_chunk_text_file_by_file(self):
        chunks = call_or_skip(core.chunk_directory, str(SAMPLE_DOCS), None, 600, 50)
        for path in dict.fromkeys(c.file_path for c in chunks):
            text = Path(path).read_text(encoding="utf-8")
            expected = call_or_skip(core.chunk_text, path, text, 600, 50)
            assert [c for c in chunks if c.file_path == path] == expected

    def test_sample_docs_respect_default_limits(self):
        chunks = call_or_skip(core.chunk_directory, str(SAMPLE_DOCS))
        assert max(len(c.text) for c in chunks) <= core.DEFAULT_MAX_CHARS
        assert len(chunks) > 12, "the sample notes have many sections; expect many chunks"

    def test_sample_docs_edge_cases(self):
        chunks = call_or_skip(core.chunk_directory, str(SAMPLE_DOCS))
        contexts = {c.heading_context for c in chunks}
        assert not any("pin a process" in ctx or "page size" in ctx for ctx in contexts)
        assert not any("show the chain" in ctx or "clip the global" in ctx for ctx in contexts)
        front = chunk_containing(chunks, "title: Embeddings and Vector Search")
        assert front.heading_context == ""
        tlb = chunk_containing(chunks, "This is a **TLB shootdown**")
        assert tlb.heading_context == "Virtual Memory and Paging > The TLB > TLB shootdowns"

    def test_f1_f4_invalid_utf8_does_not_abort_the_run(self, tmp_path):
        (tmp_path / "good.md").write_text("# Good\n\nfine\n", encoding="utf-8")
        (tmp_path / "bad.md").write_bytes(b"# Bad\n\nstill readable \xff\xfe here\n")
        chunks = call_or_skip(core.chunk_directory, str(tmp_path))
        assert {Path(c.file_path).name for c in chunks} == {"bad.md", "good.md"}
        assert any("still readable" in c.text for c in chunks)
