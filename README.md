# sift

**Local-first hybrid search engine and personal knowledge indexer.**

sift indexes your own Markdown notes, technical docs and project repositories, and answers with Okapi BM25 and dense
embeddings on ONNX Runtime, merged by Reciprocal Rank Fusion over chunks cut by a Rust (PyO3) heading-aware chunker.
It is designed to be used every day from the terminal and the browser and to explain every ranking it makes
(specified in §11 and §13; not yet built), and nothing leaves the machine.

```
index    Markdown corpus ─▶ Rust/PyO3 chunker ─▶ tokens + ONNX embeddings ─▶ SQLite

search   query ─┬─▶ tokenize ─▶ Okapi BM25 ───────────┐
                └─▶ embed ────▶ cosine similarity ────┴─▶ Reciprocal Rank Fusion (k = 60)
                                                           └─▶ local HTTP API ─▶ reader UI
```

RRF merges the two lists by rank, so BM25's unbounded scores and cosine's narrow band never have to be compared. That is
also what lets the UI say, in a searcher's words, how each result was found.

## Status

The scaffolding is complete and green. Every core algorithm is a stub, written by hand against the specification in
[IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md), which opens with a phase-by-phase build curriculum.

| Phase | Stub | Specification |
|---|---|---|
| 1 | `rust_core/src/walker.rs`, `chunker.rs` | §7, §8 |
| 2 | `sift/ranking.py`: `compute_bm25` | §3 |
| 3 | `sift/ranking.py`: `cosine_similarity` | §4 |
| 4 | `sift/ranking.py`: `reciprocal_rank_fusion` | §5 |
| 5 | `eval/ir_metrics.py`, `sift/telemetry.py`: `percentile` | §6 |
| D.1 to D.7 | workspace sync, `sift q`, field filters, provenance; Git scope, `sift rank`, `sift mcp` (specified; no stubs yet) | §10 to §16 |

Until the stubs are written, the UI answers from a bundled sample (`sift/ui/preview.json`) and says "Sample data".

## Quick start

```bash
uv venv --python 3.13 .venv && source .venv/bin/activate
uv pip install -e '.[dev,onnx]'     # maturin builds the Rust extension
export SIFT_EMBEDDER=onnx
pytest -ra                          # each skip names the stub it waits for
```

After editing Rust: `maturin develop --release`. The scaffold is healthy when these pass, stubs and all:

```bash
cargo check                         # Rust compiles; todo!() bodies type-check
pytest --collect-only -q            # every test module imports
(cd web && npm ci && npm run build) # strict TypeScript, then the production UI
```

Once the stubs are written:

```bash
sift index sample_docs
sift search "why does TCP halve its window" --explain
sift stats
sift ui --open                      # http://127.0.0.1:8765
python eval/benchmark.py            # add --embedder hash --check for the deterministic CI gate
```

## Daily driver

Four capabilities, specified in the guide (§10 to §13) and built after the core phases, turn the engine into a tool
you use on your real notes. None of them uses a language model.

- **Workspaces and live sync.** Named collections (`notes`, `docs`, `projects`) in an XDG store
  (`~/.local/share/sift/index/`, `~/.cache/sift/embeddings/`), `.gitignore` and `.siftignore` honoured, SHA-256
  change detection that re-embeds only changed chunks, and `sift watch` that makes a saved edit searchable in about
  50 ms.
- **`sift q` in any terminal.** Grep-style exit codes, results on standard output only, `--format human`, `vimgrep`
  and `jsonl`, answered by a resident process so hybrid queries skip the model load.
- **Field filters.** `path:os/ lang:rust "borrow checker"`, `tag:`, `heading:`: a deterministic parser narrows the
  candidates before any scoring, without changing a single score.
- **Why this ranked.** `sift q page fault --explain` shows which of your words drove each BM25 score and by how
  much, its cosine, and how fusion moved it: `BM25 #7 + Dense #2 → Hybrid #1`.

## Terminal, Neovim and agent integration

Specified in §11 and §14 to §16, to be built after the daily-driver layer. Every piece is read-only and local.

**fzf.** Pick a hit with a live preview; `enter` opens your editor on the line.

```bash
sift q --format vimgrep 'path:os/ "page table"' \
  | fzf --delimiter : --preview 'tail -n +{2} {1} | head -20' --bind 'enter:become($EDITOR +{2} {1})'
```

**Neovim.** `vimgrep` lines are `path:line:1: [rrf=0.0328] heading — snippet`, the quickfix format:

```vim
:cexpr system('sift q --format vimgrep tlb shootdown') | copen
```

**jq.** `jsonl` is one object per hit:

```bash
sift q --format jsonl borrow checker | jq -r '"\(.path):\(.line)  \(.heading_context)"'
```

**Git scope.** Search only what you are working on, or what changed recently:

```bash
sift q auth middleware --git-changed
sift q retry policy --since 2.weeks.ago
```

**`sift rank`.** Rank any text from standard input with no index, like a smarter `grep`:

```bash
git diff | sift rank "auth middleware"
man tcp | sift rank keepalive
sift rank --name rfc9293.txt --format vimgrep congestion < rfc9293.txt
```

**`sift mcp`.** A read-only Model Context Protocol server over standard input and output, with two tools:
`sift_search` (cited hits) and `sift_read_folio` (exact line ranges of an indexed file). Register it with any
MCP client, for example Claude Code:

```bash
claude mcp add sift -- sift mcp -c notes
```

or in a client's JSON configuration:

```json
{ "mcpServers": { "sift": { "command": "sift", "args": ["mcp", "-c", "notes"] } } }
```

## Layout

| Path | Holds |
|---|---|
| `rust_core/` | The PyO3 crate, imported as `sift._rust_core`. `lib.rs` is the finished Python boundary; `walker.rs` and `chunker.rs` are yours. |
| `sift/` | `ranking.py` (BM25, cosine, RRF), `tokenizer.py`, `embeddings.py`, `db.py`, `search.py` (pipeline and stage timing), `telemetry.py`, `cli.py`. |
| `sift/ui/` | `server.py`, the standard-library JSON server; `static/`, the built UI; `preview.json`, the sample data. |
| `eval/` | `benchmark.py` (the harness), `ir_metrics.py` (metrics, yours), `queries.json` (graded queries). |
| `web/` | The reader UI: React and TypeScript, built into `sift/ui/static/`. The build is checked in, so running it needs no Node. |
| `tests/` | Contract tests. A test whose stub still raises skips itself. |
| `sample_docs/` | Twelve notes: databases, machine learning, networking, operating systems. |

## Local API

`sift ui` serves a read-only JSON API from the page's own origin (`127.0.0.1:8765`; `--host`, `--port`). Responses are
`no-store` under a strict Content-Security-Policy. A failure is `{"error": "<message>"}` with status 400 (bad
parameter), 404, 422 (a `SiftError`) or 500. The TypeScript shapes are in `web/src/types.ts`.

| Endpoint | Returns |
|---|---|
| `GET /api/search?q=&mode=&k=` | `mode` is `hybrid`, `lexical` or `semantic`; `q` is 1 to 500 characters; `k` is 1 to 50 (default 5). `hits`, each with `rank`, `chunk_id`, `file_path`, `start_line`, `end_line`, `heading_context`, `text`, `score`, `lexical_score`, `lexical_rank`, `semantic_score` and `semantic_rank`; `timings_ms`; `source`. The UI also sends `explain=1` and `c`, which today's server ignores (guide §13.7). |
| `GET /api/document?path=` | `{path, text}` for a file the index already knows, at most 1 MB. Nothing else on disk is readable. |
| `GET /api/benchmark` | `eval/results/latest.json`, or the bundled sample (`source: "preview"`). |
| `GET /api/status` | `corpus`, `index_run`, `search_source` and `benchmark_source` (`live` or `preview`), `examples`, `rrf_k`, `version`. |
| `GET /api/stats` | Index overview and p50 / p95 latency over the last 200 queries. |

## Reader UI

A calm, full-viewport workspace on archival paper: Newsreader for what you ask and read, Plus Jakarta Sans for prose,
JetBrains Mono for measurements, all bundled, so the page makes no network requests.

- **Search.** A query, three ranking modes (Words + meaning, Meaning, Words), how many results to show, a source filter,
  and folded stage timings, beside the result stream. Each result says which of your words it contains and, when it
  is unusual, how it was found.
- **Reader.** `Enter` opens the whole note with the matched passage set apart and your words highlighted.
- **Keys.** `/` focuses the query, `↑` `↓` move through results, `Enter` opens one, `J` `K` step through matches,
  `Shift+J` `Shift+K` step through results, `Esc` leaves the box or closes the note (twice clears the search), and `?`
  opens the shortcuts.
- **Benchmarks.** Recall@3, MRR, NDCG@5 and p50 / p95 latency per mode.
- **Ready for the daily-driver backend (guide §13.7).** A Collection choice when the server reports two or more,
  filter syntax (`path:`, `tag:`, `heading:`, `lang:`) in the `?` sheet once the server reports `filters`, where each
  ranker put a result when hits carry `provenance` (the detail in the reader's Scores), and highlights for matched word forms when they carry `match_spans`. None appears until
  the server sends it.

To change it: `cd web && npm run dev` (proxies `/api` to a running `sift ui`), then `npm run build`.

## Benchmark

`python eval/benchmark.py` reports Recall@3, MRR, NDCG@5 and p50 / p95 latency for the three modes on 12 notes and 20
queries. Record your own numbers here once the stubs are written, with hardware, model and chunker settings.
