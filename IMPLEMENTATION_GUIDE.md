# sift: Build Curriculum and Implementation Specification

This document has two parts.

* **Part A, the build curriculum,** is the order to build `sift` in, and the
  engineering you should be able to explain when you are done: the data
  structures, the mathematics, the memory and latency trade-offs, the
  Rust/Python boundary, and how to know the result is good. It asks questions
  and names pitfalls. It contains no solutions: no code and no step-by-step
  algorithm.
* **Part B, the specification (§1 to §9),** is the contract the stubs and the
  tests cite. It states definitions, input domains, error conditions and
  invariants, and says nothing about how to compute them. Its section numbers
  are referenced from the source tree and are stable.

Conventions (Part B). **shall** marks a requirement that a test or the specification
itself depends on. **may** marks latitude. "Chunk" is the retrievable unit;
"document" is a file. All indices in formulas are 1-based unless stated.
$\ln$ is the natural logarithm, $\log_2$ the binary logarithm. Floating-point
quantities are IEEE-754 binary64 unless stated.

| Stub | Specification |
|---|---|
| `sift/ranking.py: compute_bm25` | §3 |
| `sift/ranking.py: cosine_similarity` | §4 |
| `sift/ranking.py: reciprocal_rank_fusion` | §5 |
| `eval/ir_metrics.py: recall_at_k, reciprocal_rank, ndcg_at_k` | §6.2 to §6.4 |
| `sift/telemetry.py: percentile` | §6.5 |
| `rust_core/src/walker.rs: walk_files` | §7 |
| `rust_core/src/chunker.rs: chunk_document` | §8.1 to §8.6 |
| `rust_core/src/chunker.rs: chunk_files` | §8.7 |

---

# Part A: Build curriculum

## A.0 How to use this part

Five phases, in dependency order, then three capstone challenges (complete at least two). Each phase
has the same shape: **goal**, **what you should be able to explain**, **data
structures**, **mathematics**, **trade-offs**, **pitfalls to settle on paper
first**, **how to verify**, and **done when**. Settle the pitfalls before you
type: most defects in this kind of system are a misread boundary condition,
not a typo.

Work loop for every phase: read the Part B sections it names; write down, in
your own words, the invariants; make the smallest piece pass its tests;
`pytest -ra` lists each skipped test with the stub it still waits for, which
is your to-do list; then measure (§1.3 gives the budgets).

### Phase gates at a glance

A phase is finished when its **gate** passes: the contract tests, then the adversarial matrix in the phase, then a
measured budget. The tests are necessary and not sufficient; the matrix is where the defects live.

| Phase | Invariants that must hold | Adversarial matrix | Measured gate |
|---|---|---|---|
| 1 Chunker | C1 to C11, O1 to O5, §8.8 A1 to A6; GIL released across the walk and the chunking | torture inputs in Phase 1 | `chunk_ms` within §1.3 on `sample_docs` |
| 2 BM25 | §3.3 items 1 to 7; one tokenizer for index and query | hand-scored toy corpus; degenerate queries and corpora | `lexical_ms` within §1.3 |
| 3 Dense | §4.3; unit-length vectors to $10^{-6}$; one embedding per distinct text | padding invariance; cache edit test | `embed_ms` and `vector_ms` within §1.3 |
| 4 Fusion | §5.3 total order; §5.5 invariance | monotone-transform test; tie matrix | `fusion_ms` negligible beside the scans |
| 5 Evaluation | §6.2 to §6.6; macro-averaging | hand-computed metrics; hypothesis gates | numbers reproduce from a clean checkout |

### Where the phases land in this repository

The plan "chunker in `lib.rs`, BM25 in `bm25.py`, embedder in `embedder.py`,
fusion in `fusion.py`" does not match the module layout the tests and stubs
are wired to. Do **not** rename modules to match it: every stub docstring, the
type stub `sift/_rust_core.pyi`, and the tests import the paths below.

| Phase | You write | Already complete (read it, do not rewrite it) |
|---|---|---|
| 1 | `rust_core/src/walker.rs`, `rust_core/src/chunker.rs` | `rust_core/src/lib.rs`: the PyO3 surface (argument validation, GIL release, type conversion, error mapping). **Start at `walker.rs`, then `chunker.rs`.** |
| 2 | `sift/ranking.py`: `compute_bm25` | `sift/tokenizer.py` (the one tokenizer), `sift/db.py` (tokens and `term_stats`) |
| 3 | `sift/ranking.py`: `cosine_similarity` | `sift/embeddings.py`: ONNX Runtime and PyTorch adapters, the `Embedder` protocol, a hashing embedder for tests |
| 4 | `sift/ranking.py`: `reciprocal_rank_fusion` | `sift/search.py` (pipeline and `StageTimer`, which already uses `time.perf_counter_ns()`), `sift/ui/server.py` |
| 5 | `eval/ir_metrics.py`, `sift/telemetry.py: percentile` | `eval/benchmark.py` (harness), `eval/queries.json` |

Two corrections to the usual picture. The HTTP bridge is the standard-library
`ThreadingHTTPServer` in `sift/ui/server.py`, not FastAPI, and a single lock
serialises searches so stage timings are honest. And the embedding engine
already exists; what is left in the dense path is the similarity function and,
as an extension, your own scan over the vectors.

Phase 5 is the smallest and purest (three metric functions and a percentile).
If you want a warm-up, or want `eval/benchmark.py` to run early, do it first;
nothing else depends on it.

---

## Phase 1: Structure-aware Markdown chunker in Rust

**Goal.** Turn a directory of notes into chunks that respect document
structure, each carrying its heading breadcrumb and the line where it starts
(§7, §8).

**You should be able to explain**
- why retrieval quality depends on chunk boundaries as much as on the ranker;
- what the breadcrumb buys at query time (a chunk that says "Timeouts" is
  ambiguous; "TCP > Congestion control > Timeouts" is not), and what it costs;
- exactly where the PyO3 boundary copies, and why it must.

**Data structures**
- A **line classifier** with a small state: either outside a fence, or inside
  one that remembers the fence character and the length of its opening run
  (§8.1). Three line kinds matter: fence line, heading line, anything else.
  Settle the transition table on paper: what each kind does in each state, and
  what it does to the heading stack.
- The **heading stack** (§8.3): levels strictly increase from bottom to top;
  what a new heading removes before it is pushed is the whole algorithm.
- A **sections list**: a section is a run of lines starting at line 1 or at a
  heading (§8.1). Chunks never cross sections (C4).
- A **position index** from a character offset to a 1-based line number, built
  once per document. §8.6 forbids rescanning a prefix per chunk; ask what
  access pattern makes a precomputed table or a moving cursor sufficient.

**Mathematics**
- A section of $L$ characters split with window $M$ and overlap $o$ advances
  by at most $M-o$ per chunk, so it yields about $\lceil (L-o)/(M-o)\rceil$
  chunks.
- Overlap multiplies stored and embedded text by roughly $M/(M-o)$ on split
  sections. Compute it for the defaults ($M=1200$, $o=150$) and for your own
  corpus, then decide whether the recall it buys is worth the index size and
  the embedding time.

**Trade-offs**
- *Characters or tokens.* The spec windows in Unicode scalar values (§8.1)
  because the chunker has no tokenizer; a token window would couple Rust to the
  embedding model. The price: the model's token limit is the real ceiling. A
  1,200-character chunk is on the order of 250 to 300 word pieces, and
  `all-MiniLM-L6-v2` truncates at 256. Measure how much of your chunks the
  model never sees.
- *Atomic code fences versus the size cap.* Keep a fenced block whole whenever
  it fits. But C3 is a hard cap: a block longer than $M$ must be split, so
  decide where, what each piece inherits (§8.2 C6), and how a reader can tell.
- *Chunk size.* Small chunks are precise and cheap to score but lose context;
  large ones carry context and dilute a dense embedding. §1.3 gives the
  latency each choice must live within.

**The boundary.** Text arrives as `&str` and leaves as owned values (§2.2).
Inside the chunker you can work on borrowed sub-slices of the input with no
per-chunk allocation; the copy happens once, when an owned `DocumentChunk` is
built, because a Python object cannot hold a borrow into Rust memory. That is
the whole of "zero-copy" here. Slicing a `&str` at an arbitrary byte index
panics on a non-boundary; lengths in the contract are in characters, not bytes
(§2.2). Work out how to keep a running character count without recounting.
Parallelism is per file (§8.7); the interpreter lock is already released by
`lib.rs`, so nothing inside the Rust region may touch a Python object (§2.5).
Whether you add a thread-pool crate or use the standard library is your call.

**Pitfalls to settle first**
- `#hashtag` is not a heading (a space or end of line must follow); a `#` line
  inside a fence is not a heading; a line with four leading spaces is not a
  fence or heading.
- A fence closes only on a run at least as long as the opener, of the same
  character, with nothing after it; an unclosed fence runs to end of file.
- A skipped level (`#` then `###`) creates no phantom entry (C5).
- `\r\n`, a trailing newline, front matter, and a byte-order mark.
- A chunk whose text begins with blank lines: where is `start_line` (§8.4
  item 5)?
- A single token longer than $M-o$ (a minified line): C10 says what you may not
  do and what you must.
- Determinism (F3): output order may not depend on thread scheduling.

**Adversarial matrix.** Each row is a test you write before you trust the chunker. Unless noted, the property is
"no panic, and C1 to C11 still hold".

| Input | What it attacks | Must hold |
|---|---|---|
| empty file; whitespace only | C1 | no chunks, no panic |
| front matter only (`---` block, nothing after) | the "ordinary text before the first heading" rule | one chunk or none, as your documented choice, never a heading |
| a 500-line fenced block, far longer than $M$ | C3 against atomicity | split, every piece $\le M$, no line lost or reordered (C10), the fence's section unchanged (C6) |
| a fence that fits in $M$, in the middle of a long section | §8.8 A1 | never divided |
| a Markdown table that fits in $M$ | §8.8 A2 | never divided between rows |
| unclosed fence; `~~~` containing a run of backticks; a fence indented 4 spaces | §8.1 fence rules | exactly the rules of §8.1 |
| `#` lines inside a fence; `#hashtag`; `#######` (seven) | §8.1 heading rules | none is a heading |
| the same document with `\n`, `\r\n` and a final line without a terminator | §8.1 line definition, §8.8 A4 | same chunk count, `heading_context` and `start_line` |
| emoji (4-byte), CJK (3-byte), combining marks, a ZWJ sequence, at and across a split point | §2.2 lengths in scalars | zero non-character-boundary slices; $|\text{text}|$ counted in scalars |
| one 5,000-character line with no whitespace | C10 | divided somewhere legal, nothing dropped |
| a skipped level (`#`, then `###`) and a heading deeper than six levels | C5 | no phantom stack entries |
| the same corpus under 1, 2 and 16 worker threads | F3 | byte-identical output |

**Walker (§7).** Small, but it fixes the order of everything downstream. The
questions: what is a hidden entry, why are symlinks neither followed nor
returned (cycles, escaping the root), why sort, and what "sorted by the
byte-wise order of the UTF-8 string" rules out.

**Verify.** `tests/test_rust_contract.py` (`TestWalkFiles`, `TestChunkText`,
`TestChunkDirectory`, `TestPyO3Surface`), `cargo clippy -- -D warnings`, and a
property check of your own: generate random Markdown and assert C3, C10 and the
overlap invariants O1 to O5 hold on every output. Time `chunk_ms` on
`sample_docs` against its §1.3 budget.

**Done when** the Rust tests no longer skip, `sift index sample_docs --rebuild`
runs, and you can draw the state machine from memory.

---

## Phase 2: Okapi BM25

**Goal.** Score one chunk against one query (§3), then consider how to score
all chunks fast.

**You should be able to explain**
- what each factor of the formula is for, by deleting it and describing what
  breaks;
- why the corpus statistics are over *chunks*, not files (§1.2);
- why a pure scoring function is worth more than a clever one.

**Mathematics**
- The formula and parameters are in §3.2. Reason about its limits: as
  $f(t,d)\to\infty$ the term-frequency factor approaches $k_1+1$, so
  repetition has diminishing returns; $b$ interpolates between ignoring chunk
  length ($b=0$) and fully normalising by it ($b=1$).
- *IDF smoothing.* The original Robertson–Spärck Jones weight is
  $\ln\frac{N-n+0.5}{n+0.5}$, which is negative once a term occurs in more
  than half the chunks. The $+1$ inside the logarithm in §3.2 keeps it
  positive for every $0\le n\le N$ (§3.3 item 3). Work out the value at
  $n=N$ and at $n=0$.
- *Defaults.* The repository uses $k_1=1.2$, $b=0.75$ (§3.2). Common
  published ranges are $k_1\in[1.2,2.0]$. Treat a value such as $1.5$ as an
  experiment to run through the Phase 5 harness, not a change to the default.

**Data structures**
- *As specified:* a scan. The pipeline loads each chunk's stored tokens and
  scores every chunk: $O(\sum_d |d|)$ (§3.4), comfortably within budget for
  151 chunks, and the reference you will test everything else against.
- *Extension: an inverted index.* A map from term to a posting list of
  `(chunk id, term frequency)` pairs sorted by chunk id, with a length array
  and the corpus constants beside it. Document frequency is the posting list's
  length. A query then touches only the postings of its own terms rather than
  every chunk. Questions to answer: memory per posting and total size as a
  function of tokens; whether delta-encoding the ids is worth the decode cost;
  how to keep it consistent with `term_stats` and with SQLite as the source of
  truth; and whether to evaluate term-at-a-time with an accumulator or
  document-at-a-time.
- *Top-$K$.* Selecting $K$ of $M$ scored items with a bounded min-heap is
  $O(M\log K)$; a full sort is $O(M\log M)$. For a query that matches few
  chunks, which dominates?
- *Pruning (advanced).* Upper-bounding each term's maximum possible
  contribution lets you stop scoring chunks that cannot enter the top $K$
  (the idea behind MaxScore and WAND). Work out the bound from §3.2 before you
  read about either.

**Tokenization, stemming and stopwords.** The tokenizer is complete and
shared by indexing and querying (§1.2, invariant 1). Adding a stemmer or a
stopword list changes every stored token list, so it is an extension that
requires `--rebuild`, not a tweak. Weigh it:
- Stemming (Porter, or the revised Snowball/Porter2) raises recall by
  conflating forms and lowers precision by over-conflating ("university",
  "universe"); it is language-specific.
- Stopword removal shrinks posting lists but BM25's IDF already pushes common
  words toward zero weight, and removal breaks queries made of stopwords.
- Whatever you choose, the query and the document must go through the same
  function, always.

**Pitfalls to settle first**
- A repeated query term contributes once (§3.2). A term absent from
  `doc_freqs` has $n=0$ (§3.3 item 2).
- Empty query, empty chunk, $N=0$: return exactly `0.0`, never raise (item 4).
- $\text{avgdl}=0$ and the division it implies.
- Float determinism: sum terms in an order that does not depend on hash
  ordering, so results are reproducible (§1.2 item 3).

**Technical-identifier analyzer (extension).** Notes and code are full of tokens the shipped tokenizer splits
apart: `page_table_walk`, `camelCase`, `O(log n)`, `0x0004`, `PyO3`. An analyzer that keeps both the exact symbol and
its sub-tokens finds a query for either. It changes every stored token list, so it is a `--rebuild` experiment judged
by Phase 5, not a tweak. Invariants to state and test:

| Input | Both of these must be retrievable |
|---|---|
| `page_table_walk` | the whole identifier, and `page`, `table`, `walk` |
| `camelCase`, `HTTPServer` | the whole identifier, and its words (decide how an acronym run splits, and say so) |
| `O(log n)`, `0x0004`, `PyO3` | the symbol as written, with no loss of digits or case-folded collisions between `PyO3` and `pyo3` unless you choose that |

The query and the document must go through the same function (§1.2 item 1), and a query that is one identifier must
rank the chunk containing it above a chunk containing only one of its parts.

**Reference gate.** Score a three-chunk toy corpus by hand for two queries (one term present in all chunks, one in
one), writing every factor ($n(t)$, $\text{idf}$, $f$, $|d|$, $\text{avgdl}$) on paper. `compute_bm25` shall match your
numbers to $10^{-9}$. Then the degenerate cases, each returning exactly `0.0` and raising nothing: a query of
stopwords only (if you add a stopword list), a term absent from the corpus, an empty query, an empty chunk, and a
corpus of one chunk ($N=1$: no division by zero, no negative weight, §3.3 item 3).

**Verify.** `TestComputeBm25`; the monotonicity and concavity properties in
§3.3 items 6 and 7; and a three-chunk corpus scored by hand on paper before you
run anything. If you build an inverted index, make a differential test: it must
agree with the brute-force scorer on random queries to within floating-point
tolerance. That brute-force function is your oracle.

**Done when** `lexical_ms` is within §1.3 and you can state, without notes, what
$k_1$ and $b$ do.

---

## Phase 3: Dense retrieval and cosine similarity

**Goal.** Compare a query embedding with chunk embeddings (§4) and understand
the engine that makes them.

**What exists.** `sift/embeddings.py` runs sentence-transformers models on ONNX
Runtime or PyTorch, behind the `Embedder` protocol, and a deterministic hashing
embedder stands in for tests. Vectors are stored exactly as produced, and are
not guaranteed to be unit length (§4.1). You write the similarity function; how
the model produces vectors is a thing to *understand*, and optionally to
re-implement.

**Mathematics**
- Cosine similarity and its convention at the zero vector are in §4.2.
- For unit vectors $\lVert\hat u-\hat v\rVert^2 = 2-2\cos(u,v)$: angular
  similarity and Euclidean distance rank identically. Why does that matter for
  the choice of index structure?
- *Pooling.* A transformer returns one vector per token. A sentence vector is
  a pooled summary. With **attention-masked mean pooling** it is
  $e=\dfrac{\sum_i m_i h_i}{\sum_i m_i}$, where $m_i\in\{0,1\}$ is the
  attention mask and padding positions are excluded. The pooling is a property
  of the model, not a free choice: all-MiniLM-L6-v2 uses mean pooling, while
  the BGE family uses the first (`[CLS]`) token. Read the model card.
- *Normalisation.* Dividing by the L2 norm makes cosine equal to a dot
  product. Do it once at write time or at load time, not per comparison.

**Trade-offs**
- *Scan cost.* $N$ chunks of dimension $D$ in float32 occupy $4ND$ bytes:
  151 chunks is under 0.25 MB, 100,000 chunks at $D=384$ is about 154 MB. A
  brute-force scan is memory-bandwidth-bound, so halving the bytes (float16,
  int8 quantisation) is a direct speed-up at some accuracy cost. Estimate the
  scan time from bandwidth, then measure it.
- *Pairwise versus matrix.* `cosine_similarity` is a pairwise function (§4.3);
  the pipeline calls it once per chunk. A matrix-vector product over
  pre-normalised vectors scores all chunks at once. Keep the pairwise function
  as the oracle and test the fast path against it.
- *Top-$K$ of dense scores.* You need the best $K$ of $N$, not a full sort:
  look at partial selection.
- *Quantised ONNX models* run faster and smaller than float32 ones and shift
  scores slightly. Whether ranking quality survives is an empirical question
  for Phase 5, on your corpus.
- *Model choice.* `SIFT_MODEL` (default `all-MiniLM-L6-v2`) picks the model at
  index time. The usual next candidate is `BAAI/bge-small-en-v1.5`: also 384
  dimensions, but a 512-token window instead of 256, `[CLS]` pooling instead of
  mean pooling, and a model-card instruction prefix for short retrieval queries
  ("Represent this sentence for searching relevant passages: "), applied to the
  query only, never to the chunks. Version 1.5 is meant to work without the
  prefix too, so measure both. Changing the model invalidates every stored
  vector, because vectors from two models are not comparable. Treat it as a
  Phase 5 experiment with a decision-log entry, not as a flip of the default.

**Extension: a from-scratch embedder behind the protocol**
1. Run an exported ONNX model with `onnxruntime`: tokenizer (WordPiece),
   session, inputs, the mask. Set the maximum sequence length deliberately and
   decide what truncation does to long chunks (see Phase 1).
2. Pool, then normalise, as above. Query-side instruction prefixes (some
   models require one) are part of correctness.
3. **Content-addressed cache.** Key each vector by the SHA-256 of the model
   identifier, the pooling and normalisation settings, and the chunk text. The
   repository already hashes each file's chunks to skip unchanged files
   (`sift/indexer.py`); a per-chunk cache additionally reuses vectors across
   renames, moved files, and the overlap regions that repeat text. A vector
   is $4D$ bytes (1,536 for $D=384$): what is the eviction policy?
4. *Parity.* Your embedder and the sentence-transformers one must agree to
   within quantisation error on the same text.

**Gate.** Beyond the tests, these properties must hold of whatever produces vectors:

| Property | Test |
|---|---|
| Unit length | $\lVert v\rVert_2 = 1 \pm 10^{-6}$ for every stored vector |
| Padding invariance | the same text embedded alone and inside a batch with much longer texts agrees to $10^{-6}$, which proves the attention mask is applied in the mean |
| Batch invariance | batch size 1 and 32 give the same vectors to $10^{-6}$ |
| Content addressing | the key is the SHA-256 of model id, pooling, normalisation and chunk text; editing one of fifty files re-embeds exactly its changed chunks (count the model calls), and a rename re-embeds nothing |
| Cache location | `$XDG_CACHE_HOME/sift`, falling back to `~/.cache/sift`; a corrupt or partial entry is ignored and rebuilt, never trusted |

**Pitfalls to settle first.** Zero vectors; mismatched dimensions (`ValueError`,
§4.3 item 3); read-only arrays (stored vectors are read-only views, §2.6);
float32 accumulation and values that round outside $[-1,1]$ (§4.3 item 1);
never mutating inputs.

**Verify.** `TestCosineSimilarity`; the reference angles in §4.3 item 6;
agreement of the matrix path with the pairwise oracle on random data.

**Done when** `vector_ms` is within §1.3 and you can explain why unnormalised
storage plus per-pair normalisation costs more than one normalisation at load.

---

## Phase 4: Reciprocal Rank Fusion and instrumentation

**Goal.** Merge two incomparable ranked lists into one (§5), and make every
stage's cost visible.

**You should be able to explain**
- why scores cannot simply be added;
- what $k$ controls, in numbers;
- what "deterministic" demands of the output order.

**Mathematics**
- $\text{RRF}(d)=\sum_i 1/(k+r_i(d))$ (§5.2). BM25 is unbounded and
  corpus-dependent; cosine lives in a narrow band. Normalising scores (min–max,
  z-score) is brittle to outliers and to the candidate pool; ranks are not.
  §5.5 item 3 is the invariance property that makes this precise.
- *Role of $k$.* At $k=60$ the first two ranks contribute $1/61$ and $1/62$, a
  ratio of about $0.984$: rank matters gently. At $k=0$ they contribute $1$ and
  $\tfrac12$: the top rank dominates. A chunk ranked first by both lists scores
  $2/61\approx 0.0328$, the maximum (§5.5 item 1), which is the value you will
  see on a top hybrid hit in the UI.
- *Candidate pool.* An item outside one list's pool is treated as absent from
  that list. The pool size (`DEFAULT_POOL_SIZE`, 50) is a recall/latency knob:
  what happens to a chunk that is excellent for one ranker and invisible to the
  other?

**Pitfalls to settle first.** Exact ties: ranks $(1,3)$ and $(3,1)$ give the
same sum, because floating-point addition is commutative; the order is then
decided by the rule in §5.3, not by arrival order. Empty lists; duplicates
(first occurrence wins, §5.4); negative $k$.

**Instrumentation**
- `sift/search.py` already wraps each stage in a `StageTimer` using
  `time.perf_counter_ns()` and reports `embed_ms`, `lexical_ms`, `vector_ms`,
  `fusion_ms`, `total_ms`. Read it before extending it. Questions: why a
  monotonic clock, why exclude model load (§6.6), what a single warm-up call
  hides, and what the search lock in `sift/ui/server.py` does for measurement
  honesty and costs in throughput.
- *Rank deltas.* The UI shows how far a result moved between two ranking modes
  (computed in the browser from consecutive responses). A per-hit
  "BM25 rank → hybrid rank" requires the backend to return each hit's rank in
  each input list, which is a new field in the API contract (see the README,
  "Local API"). Decide the sign convention and what "absent from this list"
  shows.

**Tie matrix.** With $k=60$ the pairs of ranks $(1,3)$ and $(3,1)$ tie exactly. Test, for lists of lengths $0$, $1$
and $50$: a tie, an item in one list only, an item first in both, a duplicate inside a list, and a permutation of
the arguments. In every case the output is the §5.3 total order, and applying any strictly increasing function to
the scores that produced the lists changes nothing.

**Rank deltas and stage timing.** A per-hit "words rank minus fused rank" is the difference of two integers the
backend already holds when it fuses; decide the sign, and what an item absent from one list reports. Gate the five
stage keys together: every search response carries `embed_ms`, `lexical_ms`, `vector_ms`, `fusion_ms` and `total_ms`
measured by a monotonic nanosecond clock, no stage is negative, and the stages sum to no more than `total_ms`.

**Verify.** `TestReciprocalRankFusion`; §5.5 properties; apply a strictly
increasing transform to the input scores and confirm the output is unchanged;
work a six-item example by hand.

**Done when** `fusion_ms` is negligible next to the scans and the hybrid order
for the sample queries is explicable rank by rank.

---

## Phase 5: Information-retrieval evaluation

**Goal.** Know whether the system is any good, and prove it to someone else
(§6).

**What exists.** `eval/benchmark.py` runs every query through the three modes
and reports quality and latency. `eval/queries.json` holds 20 graded queries
over the 12 notes, tagged `keyword` (5), `natural` (7), `paraphrase` (4) and
`cross` (4). You write the metrics (`recall_at_k`, `reciprocal_rank`,
`ndcg_at_k`) and `percentile`.

**Building the relevance set**
- *Scale and shape.* Grow it past 30 queries, keeping the four types:
  exact-symbol lookups (BM25 should win), conceptual paraphrases that avoid the
  note's vocabulary (dense should win), and multi-constraint or cross-note
  queries (fusion should win). These are hypotheses to test, not outcomes to
  engineer. Aim for at least eight queries of each type, so that a per-type
  mean is not decided by one query.
- *Qrels.* `eval/queries.json` is the qrels file: in TREC terms each
  `(query id, document, grade)` triple is one relevance judgment. Judgments
  are made per *file*, but retrieval returns *chunks*, so before scoring, the
  harness collapses each ranked chunk list into the order in which files
  first appear (`ranked_files` in `eval/benchmark.py`). Work out what that
  collapse hides (two relevant chunks of one note count once; a note's weak
  chunk can outrank another note's best one). Then decide whether
  chunk-level judgments would change any conclusion you draw.
- *Judgments.* Grades are 2 (the note that answers it) and 1 (covers it in
  passing). Write the judgments before you look at any ranker's output, from
  the notes alone, so the ranker cannot bias them.
- *The corpus is small.* With 12 files a ranker that guesses at random puts a
  single relevant note in the top 5 about 42% of the time ($5/12$). Recall@5 on
  this corpus saturates and discriminates poorly. Grow `sample_docs` (more
  notes, with deliberate near-duplicates and distractors) or you will be
  comparing noise. Keep a held-out subset you never tune against.

**Metrics (§6.2 to §6.5)**
- Macro-average over queries; each query weighs the same (§6.6 item 3).
- $\text{MRR}$ in §6.3 has no cutoff. "MRR@10" is a harness decision: define a
  reciprocal rank of $0$ when the first relevant item is past rank 10, and say
  so in your write-up.
- NDCG uses gain $2^{\rho}-1$ and a $1/\log_2(i+1)$ discount (§6.4); check that
  your ideal ordering handles grade ties and a list shorter than $K$.
- Latency: percentiles by linear interpolation (§6.5). $p_{95}$ needs at least
  20 samples (§6.6 item 4): 30 queries × 5 repeats is enough; 3 queries is not.
  Never average percentiles across queries.
- Results from the hashing embedder are for regression checking only (§6.6
  item 5).

**Cutoffs in the harness.** The constants `RECALL_K` (3) and `NDCG_K` (5) in
`eval/benchmark.py` set the reported cutoffs, and the UI reads the resulting
keys (`recall@3`, `ndcg@5`). Moving to Recall@5 and NDCG@10 is a coordinated
change across the harness, `web/src/types.ts`, `web/src/components/Benchmarks.tsx`
and the README. Do it as one deliberate step after the stubs pass, not midway.

**Hypothesis gates.** Partition the relevance set by what each ranker should be good at, and turn each hypothesis
into a test that reports, rather than assumes:

| Partition | Hypothesis | Gate |
|---|---|---|
| Exact symbols and identifiers | lexical beats dense on MRR@10 and nDCG@10 | per-query paired differences, with a bootstrap interval |
| Paraphrase and concept queries | dense beats lexical on the same metrics | the same |
| Multi-constraint and cross-note queries | fusion beats both single rankers | the same |

A hypothesis that fails is a finding, not a bug: record it in the decision log with the query ids. A gate that is
tuned until it passes proves nothing, so fix the queries and the metric cutoffs (MRR@10, nDCG@10) before you look at
any output. Wire the aggregate comparisons into `pytest` as a regression gate against `eval/baseline.json`, and track
$p_{50}$ and $p_{95}$ per stage beside them (§6.6: at least 20 samples per percentile). Moving the cutoffs from
Recall@3 and NDCG@5 is the coordinated change described below.

**Methodology to demonstrate**
- Report per query *type*, not just overall; a mean can hide that one ranker
  wins one type and loses another.
- Compare rankers per query (paired differences), and attach an uncertainty to
  the difference, for example a bootstrap interval. With 30 queries the
  interval will be wide: say what you can and cannot conclude.
- Record `eval/baseline.json` and use `--check` so a regression fails CI.
  Record hardware, model, chunker settings and repeat count with every number.

**Verify.** `TestIrMetrics`, `TestPercentile`, `TestSummarize`. Hand-compute
each metric for a three-item list before trusting the function.

**Done when** `python eval/benchmark.py` completes and you can defend every
number in the table, including why one ranker lost somewhere.

---

## Capstone challenges

Complete at least two. Each has an acceptance gate, and a result you cannot reproduce does not count.

These are not required by any test. They are what separates a working system
from one you can defend in an interview. Each states a goal and the questions,
not the answer. Any change to the API shape means updating
`sift/_rust_core.pyi`, the README's "Local API", and `web/src/types.ts`
together.

### E.1 Incremental indexing and a file watcher

**Goal.** Reflect an edit in the index in about the time it takes to re-embed
the chunks that changed, with no full rebuild.
- Today's unit of change is the file: `sift/indexer.py` hashes each file's
  chunks and skips unchanged files. The next unit is the chunk. What is the
  key that identifies "the same chunk" across an edit?
- Modification time is a cheap first filter, a content hash is the arbiter.
  When is mtime wrong (clock skew, restored backups, same-second edits)?
- Corpus statistics are global. Removing or adding a chunk changes $N$,
  $\text{avgdl}$ and some $n(t)$; which stored quantities must be adjusted,
  and which are derived at query time?
- Editors save by writing a temporary file and renaming it. Polling or
  OS events? How do you debounce a burst into one update?
- Searches must see a consistent index while an update is applied. What does
  the SQLite transaction (and the search lock) guarantee?
- **Correctness test:** after any sequence of edits, adds and deletes, the
  incremental index must equal a full rebuild. Generate random sequences and
  assert it.

**Acceptance gate.** On `sample_docs`, an edit to one note is reflected in both the lexical statistics and the
vector matrix in under 50 ms (measure the median of at least 20 edits, warm, model already loaded; the re-embedding
is the cost, so say how many chunks changed). The equality test above passes on at least 200 random edit
sequences, including a delete followed by a re-add and a rename.

### E.2 Backend match spans for highlighting

**Goal.** Return, per hit, the exact regions of the text that matched, so the
UI can highlight morphological matches (a query for "paging" marking "page" and
"pages") instead of exact words.
- The browser currently highlights literal query words (`web/src/lib/text.ts`).
  Matching stems needs the analyzer on both sides, and a tokenizer that keeps
  each token's position, which the current one discards.
- Offsets are the hard part. JavaScript strings are UTF-16; the contract
  elsewhere counts Unicode scalar values (§2.2). Choose one unit, document it,
  and convert at the boundary. What breaks for an emoji or a combining mark?
- Overlapping and adjacent spans must merge into one highlight.
- Decide the response field, its unit, and what happens in semantic mode where
  no term matched (a ranking reason is not a highlight).

**Acceptance gate.** Spans round-trip: slicing the returned text at every span in the unit you chose yields exactly
the matched word forms. A query for "paging" marks "page" and "pages" in a chunk that contains them; a chunk with an
emoji or a combining mark before the match gives the same span text; adjacent and overlapping spans arrive merged;
and the UI's current literal-word highlighting is replaced without a visible regression on the sample queries.

### E.3 A Rust scoring kernel

**Goal.** Move the inner scan into `rust_core` with PyO3 and data parallelism,
and prove whether it pays.
- Start from a measurement, not a hope: build a synthetic corpus of about
  10,000 chunks with a seeded generator (the sample notes give only 151), then
  record Python `p95` for each stage across at least 20 samples, cold and
  warm.
- Data layout is the lever: one contiguous row-major `f32` matrix read without
  copying will beat any per-element conversion of Python objects. What does the
  call overhead and the lock release cost per query, and at what corpus size
  does it break even?
- Parallel top-$K$: each thread keeps its own bounded heap; how are $T$ heaps
  merged, and what does that do to determinism (§1.2 item 3)?
- An abi3 wheel cannot assume a CPU instruction set at build time. What do you
  do about SIMD?
- New crates are yours to add to `rust_core/Cargo.toml`. Rayon is the
  standard choice for data parallelism; its work-stealing pool decides how
  chunks of the scan are split, and you decide how results are combined. The
  `numpy` crate borrows a NumPy array as a read-only view, so you never
  convert the matrix element by element. Keep the pure Python function as the
  oracle and write a differential test against it.
- Report Python versus Rust `p95` with the corpus size, thread count and
  hardware, and be ready to explain Amdahl's law for your numbers.

**Acceptance gate.** On at least 10,000 synthetic chunks from a seeded generator, the Rust kernel returns the same
top $K$ as the Python oracle on 1,000 random queries (ties broken as §1.2 item 3 requires), and the report states
whether it won: Python NumPy versus Rust `p95` at one, two and all threads, cold and warm, on named hardware. A kernel
that loses is a valid result if the numbers explain why.

---

## Portfolio notes

A reviewer will not read your code first. Give them a README with the
architecture diagram and one results table (Recall, MRR, NDCG, $p_{50}$,
$p_{95}$ for the three modes, with hardware and settings); a short decision
log (why this chunk size, why $k=60$, why this model); and one honest failure
analysis: a query where your system loses, and why. Everything in Part A is a
question you should be able to answer aloud.

### Primary sources

Read the original papers, not summaries of them. Each one maps to a phase.

| Phase | Source |
|---|---|
| 2 | S. Robertson and H. Zaragoza, *The Probabilistic Relevance Framework: BM25 and Beyond*, Foundations and Trends in Information Retrieval 3(4), 2009. |
| 2 | C. D. Manning, P. Raghavan and H. Schütze, *Introduction to Information Retrieval*, Cambridge University Press, 2008: ch. 1–2 (inverted index, tokenization, stemming), ch. 6 (term weighting), ch. 8 (evaluation), ch. 11 (probabilistic retrieval and BM25). |
| 2 | M. F. Porter, "An algorithm for suffix stripping", *Program* 14(3), 1980. |
| 2 | H. Turtle and J. Flood, "Query evaluation: strategies and optimizations", *Information Processing & Management* 31(6), 1995 (MaxScore); A. Broder et al., "Efficient query evaluation using a two-level retrieval process", CIKM 2003 (WAND). |
| 3 | N. Reimers and I. Gurevych, "Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks", EMNLP 2019. |
| 3 | S. Xiao et al., "C-Pack: Packaged Resources To Advance General Chinese Embedding", arXiv:2309.07597, 2023 (the BGE models). |
| 4 | G. V. Cormack, C. L. A. Clarke and S. Büttcher, "Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods", SIGIR 2009. |
| 5 | K. Järvelin and J. Kekäläinen, "Cumulated gain-based evaluation of IR techniques", *ACM Transactions on Information Systems* 20(4), 2002. |
| 5 | M. D. Smucker, J. Allan and B. Carterette, "A comparison of statistical significance tests for information retrieval evaluation", CIKM 2007. |

---

# Part B: Specification

## 1. System architecture

### 1.1 Components

```
                  index time                                  query time
   ┌──────────────────────────────────┐      ┌────────────────────────────────────────┐
   │ directory ─▶ walk_files (Rust)   │      │ query ─▶ tokenize ─▶ BM25 scan ─┐      │
   │          ─▶ chunk_files (Rust)   │      │      └─▶ embed ─▶ cosine scan ──┤      │
   │          ─▶ tokenize, embed (Py) │      │                       fuse (RRF)◀┘      │
   │          ─▶ SQLite (Py)          │      │                       ─▶ hydrate ─▶ hits│
   └──────────────────────────────────┘      └────────────────────────────────────────┘
```

| Component | Language | Responsibility |
|---|---|---|
| `rust_core::walker` | Rust | Select files under a root (§7). |
| `rust_core::chunker` | Rust | Split documents into chunks (§8). |
| `rust_core::lib` | Rust | PyO3 surface: argument validation, GIL release, type conversion, error mapping. Already complete. |
| `sift.tokenizer` | Python | The single tokenizer for indexing and querying (§3.1). Complete. |
| `sift.embeddings` | Python | Text to vector (§4.1). Complete. |
| `sift.db` | Python | SQLite persistence and corpus statistics (§2.6). Complete. |
| `sift.ranking` | Python | BM25, cosine similarity, RRF (§3 to §5). |
| `sift.search` | Python | Pipeline orchestration and stage timing. Complete. |
| `eval/ir_metrics.py`, `sift.telemetry.percentile` | Python | Offline metrics (§6). |
| `sift.ui.server` | Python | JSON bridge for the browser UI. Falls back to recorded fixture data while any stub above raises. Complete. |

### 1.2 Invariants of the whole system

1. **One tokenizer.** Documents and queries are tokenized by the same function
   (§3.1). A change to it invalidates every stored token list.
2. **Chunks are the unit of retrieval.** A BM25 "document" is a chunk. Corpus
   statistics are computed over chunks.
3. **Determinism.** For a fixed corpus, configuration and embedder, the
   ordered list of search results is identical across runs and across thread
   counts.
4. **Failure transparency.** A stub that is not yet written surfaces as
   `NotImplementedError` (Python) or a Rust `todo!()` panic. Nothing in the
   pipeline substitutes a default value for a missing result.
5. **Purity.** `compute_bm25`, `cosine_similarity`, `reciprocal_rank_fusion`
   and the metrics of §6 are pure functions: no I/O, no global state, no
   mutation of arguments.

### 1.3 Latency budget

Targets for the reference corpus (12 files, 151 chunks, 384-dimensional
embeddings, warm process, single query, one laptop-class CPU core for the
scans). A stage exceeding its budget is a defect to investigate, not a
failure of correctness. The stage names are those reported by
`sift.search` and shown in the UI.

| Stage | Key | Budget |
|---|---|---|
| File walk and chunk (index time, whole corpus) | `chunk_ms` | ≤ 10 ms |
| Query embedding | `embed_ms` | ≤ 10 ms |
| BM25 scan | `lexical_ms` | ≤ 5 ms |
| Cosine scan | `vector_ms` | ≤ 3 ms |
| RRF fusion (candidate pool of 50 per list) | `fusion_ms` | ≤ 0.1 ms |
| End to end | `total_ms` | p95 ≤ 25 ms |

Scan stages shall scale at most linearly in the number of chunks $N$.

---

## 2. Data contracts and the Rust/Python boundary

### 2.1 Chunk schema

The logical schema of a chunk, and its realisation in the code:

| Logical field | Code field (`DocumentChunk`, `Chunk`, SQLite `chunks`) | Type | Definition |
|---|---|---|---|
| `doc_id` | `(file_path, chunk_index)`; persisted as `chunks.id` | `str`, `int` | Identity of a chunk. `chunk_index` is the 0-based position of the chunk within its file. |
| `file_path` | `file_path` | `str` | The walker's path for the file (§7, W5). |
| `heading_path` | `heading_context` | `str` | Titles of the headings in scope at the chunk's first line, outermost first, joined by `" > "`. Empty when none. |
| `line_start` | `start_line` | `int ≥ 1` | 1-based line in the original file of the first character of `text`. |
| `line_end` | derived | `int ≥ line_start` | `start_line` plus the number of newline characters in `text` after removing trailing newlines. Computed by the consumer; not stored. |
| `text` | `text` | `str` | A contiguous substring of the file, subject to §8. |

`DocumentChunk` is immutable. Two chunks are equal iff all five stored fields
are equal.

### 2.2 Ownership and memory

* All chunk data is created by Rust as owned values. No Python object holds a
  borrow into Rust memory, and no Rust code holds a borrow into Python memory
  after a call returns.
* Text enters Rust as UTF-8 (`&str`) and leaves as Python `str` (a copy). A
  `DocumentChunk` is converted to one Python object per chunk when the call
  returns, after the interpreter lock has been reacquired.
* Lengths and positions are measured in Unicode scalar values (Python
  `len(str)`), never in bytes. A byte offset shall never be exposed across the
  boundary.
* Embedding vectors never cross the boundary. They live in Python as
  `numpy.float32` arrays and are persisted as little-endian `float32` BLOBs
  (§2.6).

### 2.3 Serialization

There is no serialization layer. Values cross the boundary through PyO3's
native conversions only:

| Python | Rust |
|---|---|
| `str` (path) | `PathBuf` |
| `list[str] \| None` (extensions) | `Option<Vec<String>>` |
| `int ≥ 0` | `usize`; a negative value raises `OverflowError` before any Rust code of the stub runs |
| return `list[str]` | `Vec<String>` from `PathBuf` by lossy UTF-8 conversion |
| return `list[DocumentChunk]` | `Vec<DocumentChunk>` |

### 2.4 Errors and panics

| Condition | Python exception | Raised by |
|---|---|---|
| `root` does not exist | `FileNotFoundError` | `lib.rs` |
| `root` is not a directory | `NotADirectoryError` | `lib.rs` |
| `max_chars == 0` or `overlap >= max_chars` | `ValueError` | `lib.rs` |
| `io::Error` returned by a stub | matching `OSError` subclass | PyO3 |
| `todo!()` or any other Rust panic | `pyo3_runtime.PanicException` (derives from `BaseException`) | PyO3 |

The release profile shall keep `panic = "unwind"`; with `abort` a panic would
terminate the interpreter.

### 2.5 Concurrency

* `walk_files` and `chunk_directory` execute their Rust bodies with the
  interpreter lock released. Code inside that region shall not touch any
  Python object.
* Output order shall not depend on scheduling (§7 W6, §8.7 F3).

### 2.6 Persistence contract (Python side)

| Table | Content |
|---|---|
| `files` | One row per indexed file; content hash for incremental indexing. |
| `chunks` | `id`, `file_id`, `chunk_index`, `start_line`, `heading_context`, `text`, `tokens` (space-joined tokenizer output), `n_tokens`, `embedding` (BLOB). |
| `term_stats` | `(term, doc_freq)`. |

Embedding BLOB: contiguous little-endian IEEE-754 `float32`, length
$4d$ bytes. Vectors read back are zero-copy, read-only views.

---

## 3. Lexical retrieval: Okapi BM25

### 3.1 Inputs

* **Tokenizer.** $\text{tokenize}(x)$ = the maximal runs of word characters
  (Unicode letters, digits, `_`) of $\text{casefold}(x)$, in order, with
  repetition.
* $q$: the token sequence of the query. $d$: the token sequence of one chunk.
* $N$: number of chunks in the corpus.
* $|d|$: number of tokens of $d$; $\text{avgdl} = \frac{1}{N}\sum_{d} |d|$.
* $n(t)$: number of chunks containing term $t$ at least once.
* $f(t,d)$: number of occurrences of $t$ in $d$.

### 3.2 Definition

$$
\text{score}(q,d)=\sum_{t\in \text{set}(q)} \text{IDF}(t)\cdot
\frac{f(t,d)\,(k_1+1)}{f(t,d)+k_1\left(1-b+b\,\dfrac{|d|}{\text{avgdl}}\right)}
$$

$$
\text{IDF}(t)=\ln\!\left(1+\frac{N-n(t)+\tfrac12}{n(t)+\tfrac12}\right)
$$

Parameters and defaults: $k_1 = 1.2$ (term-frequency saturation, $k_1 \ge 0$),
$b = 0.75$ (length normalisation, $0 \le b \le 1$). $\text{set}(q)$ is the set
of distinct tokens of $q$: a term repeated in the query contributes once.

### 3.3 Requirements

1. A term with $f(t,d)=0$ contributes $0$.
2. A term absent from `doc_freqs` has $n(t)=0$.
3. $\text{IDF}(t) > 0$ for every $t$ and every $0 \le n(t) \le N$.
4. Degenerate inputs return exactly `0.0` and do not raise: empty query,
   empty document, $N = 0$.
5. The score is non-negative, finite, and returned as a Python `float`.
6. With $b = 0$ the score is independent of $|d|$. With $k_1 = 0$ each
   matching term contributes exactly its IDF.
7. For fixed $|d|$ the score is strictly increasing and strictly concave in
   $f(t,d)$ when $k_1 > 0$. For fixed $f(t,d)>0$ and $b>0$ it is strictly
   decreasing in $|d|$.

### 3.4 Complexity

Scoring one chunk is $O(|q| + |d|)$ time. A full scan is
$O\!\left(\sum_d |d|\right)$ and shall not be asymptotically worse.

---

## 4. Dense retrieval: cosine similarity

### 4.1 Embedding geometry

An embedder maps text to $e\in\mathbb{R}^{D}$ ($D$ fixed per index; 384 for
the reference model). Vectors are stored exactly as the embedder produced
them. Some embedders return unit vectors, others do not; correctness shall not
depend on which.

For $u \ne 0$ define the unit vector $\hat u = u/\lVert u\rVert_2$. Retrieval
uses the angle between embeddings, not their lengths.

### 4.2 Definition

For $u,v\in\mathbb{R}^{D}$ with $u\neq 0$ and $v\neq 0$:

$$
\cos(u,v)=\frac{\langle u,v\rangle}{\lVert u\rVert_2\,\lVert v\rVert_2}
=\langle \hat u,\hat v\rangle,
\qquad \langle u,v\rangle=\sum_{i=1}^{D}u_i v_i,\quad
\lVert u\rVert_2=\sqrt{\langle u,u\rangle}
$$

By convention $\cos(u,v)=0$ when $u=0$ or $v=0$.

### 4.3 Requirements

1. The result lies in $[-1,1]$ for all finite inputs, including those whose
   rounded intermediate values would leave the interval.
2. $\cos(u,v)=\cos(v,u)$; $\cos(\alpha u,v)=\cos(u,v)$ for every $\alpha>0$.
3. Inputs of different length raise `ValueError`. Equal-length empty inputs
   are outside the domain.
4. Inputs are 1-D `numpy` arrays (any floating dtype, including read-only
   arrays) or sequences of `float`. They are never modified.
5. The return value is a Python `float`, never `nan`.
6. Reference angles: identical direction gives $1$, orthogonal gives $0$,
   opposite direction gives $-1$.

### 4.4 Complexity

$O(D)$ time and $O(1)$ additional space per pair. A full scan is $O(ND)$.

---

## 5. Rank fusion: Reciprocal Rank Fusion

### 5.1 Problem

BM25 scores are unbounded and corpus-dependent; cosine scores lie in
$[-1,1]$. The two score distributions are not commensurable, so fusion shall
use only the **rank** of each item in each list.

### 5.2 Definition

Let $R_1,\dots,R_m$ be ranked lists of distinct chunk ids ($m=2$ here: lexical,
semantic), best first, and $r_i(d)\in\{1,2,\dots\}$ the 1-based position of
$d$ in $R_i$. For an integer constant $k\ge 0$ (default $k=60$):

$$
\text{RRF}(d)=\sum_{i=1}^{m}\mathbf{1}[d\in R_i]\;\frac{1}{k+r_i(d)}
$$

An item absent from a list contributes $0$ for that list. The universe is
$\bigcup_i R_i$.

### 5.3 Output order and tie-breaking

1. The output contains every id of the universe exactly once, as
   `(chunk_id, score)` with `chunk_id: int` and `score: float`.
2. The order is the total order: score descending, then chunk id ascending.
3. Scores are compared as computed binary64 values. With $m=2$ the sum is
   commutative in floating point, so two items with the same pair of
   contributions tie exactly.
4. The output is deterministic: it does not depend on hash ordering or on the
   order in which equal-scored items were first seen.

### 5.4 Input domain

* Input lists may be empty. Both empty gives an empty output.
* A list is assumed free of duplicates. If an id occurs more than once in a
  list, only its first occurrence defines $r_i(d)$.
* $k<0$ raises `ValueError`.

### 5.5 Properties

1. $0<\text{RRF}(d)\le \dfrac{m}{k+1}$.
2. $\text{RRF}$ is strictly decreasing in each $r_i(d)$.
3. Invariance: any strictly monotone transformation of the scores that
   produced $R_i$ leaves the output unchanged.
4. Larger $k$ reduces the ratio between the contributions of two different
   ranks; $k=0$ gives plain reciprocal rank.

### 5.6 Complexity

$O\!\left(\sum_i |R_i| + |U|\log|U|\right)$ time, $|U|$ the universe size.

---

## 6. Offline evaluation

### 6.1 Ground truth and ranked lists

* A **query** has judgments $\text{rel}:\mathcal{D}\to\mathbb{Z}_{\ge 0}$ over
  document identifiers (file names). Documents not listed have grade $0$. A
  document is **relevant** iff its grade is $>0$. Let
  $\mathcal{R}=\{d:\text{rel}(d)>0\}$.
* A **ranked list** $\pi=(\pi_1,\dots,\pi_n)$ is a sequence of distinct
  document identifiers, best first. A chunk ranking is converted to a ranked
  list by replacing each chunk with its file and keeping only the first
  occurrence of each file, preserving order.
* Cutoff $K\in\mathbb{Z}_{\ge1}$. $\pi$ may be shorter than $K$.

Common errors, applying to all three metrics: `ValueError` if $\mathcal{R}=\varnothing$,
if $\pi$ contains a repeated identifier, or if any grade is negative.
`ValueError` if $K<1$ for the metrics that take $K$.

### 6.2 Recall@K

$$
\text{Recall@}K(\pi)=\frac{\left|\mathcal{R}\cap\{\pi_1,\dots,\pi_{\min(K,n)}\}\right|}{|\mathcal{R}|}
\in[0,1]
$$

### 6.3 Reciprocal rank and MRR

$$
\text{RR}(\pi)=\begin{cases}\dfrac{1}{\min\{i:\pi_i\in\mathcal{R}\}} & \text{if such } i \text{ exists}\\[2mm] 0 & \text{otherwise}\end{cases}
\qquad
\text{MRR}=\frac{1}{|Q|}\sum_{q\in Q}\text{RR}(\pi^{(q)})
$$

`mean` of an empty sequence raises `ValueError`.

### 6.4 NDCG@K

With gain $g(\rho)=2^{\rho}-1$ and discount $\delta(i)=1/\log_2(i+1)$:

$$
\text{DCG@}K(\pi)=\sum_{i=1}^{\min(K,n)} g(\text{rel}(\pi_i))\,\delta(i),
\qquad
\text{NDCG@}K(\pi)=\frac{\text{DCG@}K(\pi)}{\text{IDCG@}K}
$$

$\text{IDCG@}K$ is $\text{DCG@}K$ of the ideal list: all judged documents
ordered by grade descending, truncated at $K$. $\text{NDCG@}K\in[0,1]$, equal
to $0$ when no relevant document is within the first $K$ positions, and equal
to $1$ when $\pi$ is an ideal ordering. Documents beyond position $K$ have no
influence.

### 6.5 Percentiles and latency reporting

For $n\ge1$ samples with order statistics $x_{(1)}\le\dots\le x_{(n)}$ and
$q\in[0,100]$, let $h=(n-1)\,q/100$. The $q$-th percentile (linear
interpolation between closest ranks) is

$$
P_q = x_{(\lfloor h\rfloor+1)}+(h-\lfloor h\rfloor)\left(x_{(\lceil h\rceil+1)}-x_{(\lfloor h\rfloor+1)}\right)
$$

It returns a Python `float`, does not reorder or otherwise modify its input,
and raises `ValueError` if $n=0$ or $q\notin[0,100]$.

Reported latency: $p_{50}=P_{50}$, $p_{95}=P_{95}$ of per-query wall time,
per stage and for `total_ms`.

### 6.6 Measurement protocol

1. Latency samples are taken in a warm process: model loading is excluded and
   each query set is repeated $r\ge1$ times (default $r=5$).
2. Timing uses a monotonic clock. A stage that did not run for a query is
   recorded as absent, not as zero.
3. Quality metrics are averaged over queries (macro average); each query has
   equal weight.
4. A percentile is only meaningful for a sample size large enough to contain
   the tail: at least 20 samples for $p_{95}$.
5. Results produced with the hashing embedder are for regression testing and
   shall not be reported as retrieval quality.

---

## 7. File discovery: `walk_files`

Signature: `walk_files(root: &Path, extensions: &[String]) -> io::Result<Vec<PathBuf>>`.

Preconditions (established by `lib.rs`): `root` exists and is a directory;
`extensions` is non-empty, lowercase ASCII, without leading dots.

* **W1. Recursion.** Every regular file at any depth below `root` is
  considered.
* **W2. Selection.** A file is returned iff it has an extension (the text
  after its final `.`, non-empty) and that extension equals, compared
  ASCII-case-insensitively, a member of `extensions`. Files with no
  extension, and dotfiles whose only dot is the leading one, never match.
  Directories are never returned.
* **W3. Hidden entries.** Any entry whose own name starts with `.` is excluded
  and, if a directory, not descended into. `root` itself is never excluded on
  the basis of its name.
* **W4. Symbolic links.** Symbolic links are neither followed nor returned.
* **W5. Path form.** Each returned path is `root` joined with the path
  relative to `root`. Paths are not canonicalised and not made absolute.
* **W6. Order.** The result is sorted ascending by the byte-wise lexicographic
  order of the UTF-8 encoding of the path string, and is identical across
  runs and independent of thread scheduling.
* **W7. Tolerance.** An entry that cannot be read (permissions, removal during
  the walk) is omitted. It does not abort the walk, panic, or raise.

An empty result is a valid result. Complexity: $O(E)$ to visit $E$ entries
plus $O(F\log F)$ to order $F$ results.

---

## 8. Chunking

### 8.1 Definitions

* Text $T$ is a sequence of Unicode scalar values. $|x|$ is the number of
  scalar values of $x$ ("characters").
* A **line** is a maximal run of characters not containing `\n`, plus its
  terminating `\n` if present. Lines are numbered from 1. A `\r` immediately
  before `\n` is part of the terminator.
* A text is **Markdown** iff the final extension of `file_path` equals, ASCII
  case-insensitively, `md`, `markdown` or `mdx`. All other texts are
  **plain**.
* Parameters: $M=$ `max_chars` $\ge1$, $o=$ `overlap` with $0\le o<M$.
* A **fence line** (Markdown only) has at most 3 leading spaces followed by a
  run of at least three identical characters, all backticks or all tildes. A
  fence **opens** at such a line when no fence is open, and **closes** at the
  first later line that has at most 3 leading spaces, a run of the same
  character at least as long as the opening run, and only whitespace after
  it. An unclosed fence extends to the end of $T$.
* An **ATX heading line** is a line outside any fence with at most 3 leading
  spaces, then $L\in[1,6]$ consecutive `#`, then a space, a tab, or the end of
  the line. Its **title** is the remainder of the line with surrounding
  whitespace removed and, if present, a trailing run of `#` preceded by
  whitespace removed. Lines inside a fence are never headings. Underlined
  (setext) headings and headings inside block quotes or list items are not
  headings.
* A **section** is a maximal run of consecutive lines beginning either at line
  1 or at a heading line, and ending before the next heading line. Plain
  texts form a single section.

### 8.2 Contract

`chunk_document(file_path, text, cfg) -> Vec<DocumentChunk>`.

* **C1. Blank input.** If $T$ contains no non-whitespace character the result
  is empty. No chunk of any input has a text that is empty or only whitespace.
* **C2. Index.** `chunk_index` of the chunks of a document is
  $0,1,\dots,c-1$ in output order. `file_path` is copied unchanged to every
  chunk.
* **C3. Size cap.** $|\text{text}|\le M$ for every chunk, including any
  overlap region (§8.5). Chunk boundaries fall on character boundaries.
* **C4. Heading starts a chunk.** The heading line of a section is the first
  line of the first chunk of that section and is part of its `text`. A chunk
  never contains text of two different sections. A section's text preceding
  the first heading belongs to a section with an empty breadcrumb.
* **C5. Heading stack.** Maintain a stack of `(level, title)`. On a heading of
  level $L$ every entry with level $\ge L$ is removed, then `(L, title)` is
  pushed. Skipped levels create no entries. `heading_context` of a chunk is
  the titles of the stack, bottom to top, joined by `" > "`, evaluated after
  processing the heading (if any) on the chunk's first line, and is identical
  for all chunks of one section. It is empty for plain texts and for text
  before the first heading.
* **C6. Fences.** No line inside a fence changes the stack or opens a section.
  Text inside a fence belongs to the section that contains the fence.
* **C7. Splitting.** A section with $|\text{trim}(S)|\le M$ yields exactly one
  chunk. A longer section yields at least two chunks, each satisfying C3.
* **C8. Overlap.** See §8.5.
* **C9. Line numbers.** See §8.4.
* **C10. Completeness.** The concatenation of all chunk texts (overlap
  removed) contains every non-whitespace character of $T$ in order. A maximal
  run of non-whitespace characters of length at most $M-o$ is never divided
  between two chunks. Content is never reordered.
* **C11. Plain texts.** For a plain text, no line is a heading, every
  `heading_context` is empty, and the text is still divided by C3 and C7.

Behaviour not fixed by this contract and left to the implementation, which
shall document its choice: whether chunk text is trimmed of surrounding
whitespace (subject to C9), treatment of YAML front matter (it is ordinary
text before the first heading), and a leading byte-order mark.

### 8.3 Heading stack: stated formally

Let headings occur at lines $h_1<h_2<\dots$ with levels $L_j$ and titles
$s_j$. The stack $S_j$ after heading $j$ satisfies
$S_j=\left(S_{j-1}\setminus\{(\ell,s):\ell\ge L_j\}\right)\cup\{(L_j,s_j)\}$
with $S_0=\varnothing$, where entries are ordered by insertion. Levels in
$S_j$ are strictly increasing from bottom to top.

### 8.4 Line-number guarantees

1. `start_line` is the 1-based number of the line of the original file that
   contains the first character of `text`.
2. $1\le\text{start\_line}\le$ number of lines of $T$.
3. For chunks $c_i,c_{i+1}$ of one document, in order:
   $\text{start\_line}(c_i)\le\text{start\_line}(c_{i+1})$.
4. A chunk that opens with a heading line has `start_line` equal to that
   heading's line number.
5. The first line of `text`, after trimming, occurs within the source line
   numbered `start_line`.

### 8.5 Overlap invariants

Let the chunks of one section be $c_1,\dots,c_m$.

1. **O1.** If $o=0$ the chunk texts are pairwise disjoint substrings of the
   section, in order.
2. **O2.** If $o>0$ and $m>1$, for each $j>1$ there is $\ell_j$ with
   $1\le\ell_j\le o$ such that the first $\ell_j$ characters of $c_j$ equal the
   last $\ell_j$ characters of $c_{j-1}$.
3. **O3.** The first chunk of a section has no such prefix: overlap never
   spans two sections.
4. **O4.** Overlap never moves a chunk's start before the start of the chunk
   preceding it (§8.4 item 3).
5. **O5.** With the same input, the total length of all chunk texts is
   strictly larger for $o>0$ than for $o=0$ whenever some section is split.

### 8.6 Complexity

$O(|T|)$ time and $O(|T|)$ auxiliary space per document. Computing
`start_line` shall not rescan a prefix of $T$ per chunk.

### 8.7 Multi-file chunking: `chunk_files`

`chunk_files(paths: &[PathBuf], cfg: &ChunkConfig) -> Vec<DocumentChunk>`. It
runs with the interpreter lock released.

* **F1. Equivalence.** The chunks of a file equal
  `chunk_document(path, decoded, cfg)`, where `decoded` is the file's bytes
  decoded as UTF-8 with each invalid sequence replaced by U+FFFD. Invalid
  UTF-8 never causes a file to be skipped or the run to abort.
* **F2. Path.** `file_path` of every chunk is the string form of the path as
  supplied in `paths` (lossy UTF-8 conversion, as §2.3).
* **F3. Order and determinism.** Output is grouped by file in the order of
  `paths`; the chunks of one file are contiguous and in `chunk_index` order.
  The output is identical across runs and independent of the number of
  threads.
* **F4. Failure isolation.** A file that cannot be read contributes no chunks.
  It does not abort the run, panic, or raise.

`chunk_directory(root, extensions, max_chars, overlap)` is `walk_files`
(§7) followed by `chunk_files` (§8.7) on its result.

### 8.8 Conformance gates beyond the contract

These follow from the contract and the product's promise to quote a note exactly. The contract tests do not all
check them, so you shall.

* **A1. Fences that fit are whole.** A fenced block (opening line to closing line, or to end of text if unclosed)
  of at most $M$ characters is never divided between two chunks. A longer block is divided (C3 governs) and each
  piece keeps the section's `heading_context` (C6).
* **A2. Tables that fit are whole.** A run of consecutive table lines (a header row, a delimiter row, then body rows)
  of at most $M$ characters is never divided between two chunks.
* **A3. Line round trip.** For every chunk, the source lines from `start_line` through
  $\text{start\_line}+\text{lines}(\text{text})-1$ contain the chunk's text byte for byte, apart from trimming and
  the overlap prefix of §8.5. The last line of a chunk, `end_line`, is therefore derivable and is what the API
  reports.
* **A4. Terminator neutrality.** The same document with `\n`, with `\r\n`, and with a missing final terminator
  yields the same number of chunks, the same `heading_context` per chunk, and the same `start_line` per chunk.
* **A5. Scalar-value safety.** Lengths and cut points are counted in Unicode scalar values. No input, including
  text of 4-byte emoji, 3-byte CJK, combining marks and zero-width-joiner sequences, causes a slice at a
  non-character boundary.
* **A6. Parallel determinism.** `chunk_directory` output is byte-identical for one worker thread and for many.

---

## 9. Conformance

### 9.1 Mapping from specification to tests

| Specification | Tests |
|---|---|
| §3 | `tests/test_ranking.py::TestComputeBm25` |
| §4 | `tests/test_ranking.py::TestCosineSimilarity` |
| §5 | `tests/test_ranking.py::TestReciprocalRankFusion` |
| §6.2 to §6.4 | `tests/test_infrastructure.py::TestIrMetrics` |
| §6.5 | `tests/test_infrastructure.py::TestPercentile`, `::TestSummarize` |
| §7 (W1 to W7) | `tests/test_rust_contract.py::TestWalkFiles` |
| §8.2 to §8.6 | `tests/test_rust_contract.py::TestChunkText` |
| §8.7 | `tests/test_rust_contract.py::TestChunkDirectory` |
| §8.8 (A1 to A6) | your own tests: the adversarial matrix in Phase 1 (not supplied) |
| §2.4 | `tests/test_rust_contract.py::TestPyO3Surface` |

A test whose stub still raises is skipped, not failed; `pytest -ra` lists each
skip with the stub it waits for. The tests are necessary, not sufficient: the
requirements above are the specification.

### 9.2 Completion criteria

1. `cargo check` and `cargo clippy -- -D warnings` succeed.
2. After `maturin develop`, `pytest -ra` reports no skipped test whose reason
   names a stub.
3. `sift index sample_docs --rebuild` followed by `sift search` returns live
   results, and `sift ui` reports "Live index" instead of "Sample data".
4. `python eval/benchmark.py` completes and reports §6.2 to §6.5 values for
   the three ranking modes.
