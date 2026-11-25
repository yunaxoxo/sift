---
title: Embeddings and Vector Search
tags: [ml, retrieval, ann]
---

Reading notes for the retrieval part of the course project. The front matter
above and this paragraph come before the first heading on purpose.

# Embeddings and Vector Search

An embedding model maps a piece of text to a point in a high-dimensional space
(a few hundred to a few thousand dimensions) in such a way that texts with
similar *meaning* land close together. Search then becomes geometry: embed the
query and find the nearest stored vectors.

## Where the vectors come from

A transformer encoder produces one vector per token. A sentence embedding
collapses these into one vector, usually by **mean pooling** over the token
vectors, or by taking the vector of a special `[CLS]` token. The model is
trained contrastively: pairs that belong together (a question and its answer,
two paraphrases) are pulled close, and unrelated pairs in the same batch are
pushed apart.

Practical points:

- A model has a maximum input length (256 word-pieces for `all-MiniLM-L6-v2`).
  Anything longer is silently truncated, so long documents must be split into
  chunks first.
- Some models expect a prefix such as `query: ` or `passage: `. Leaving it out
  quietly costs accuracy.
- Vectors from two different models are not comparable. Changing the model means
  re-embedding the whole corpus.

## Measuring similarity

| metric             | formula                 | notes                                        |
|--------------------|-------------------------|----------------------------------------------|
| cosine similarity  | a·b / (‖a‖ ‖b‖)         | compares direction only, ignores length      |
| dot product        | a·b                     | cosine multiplied by both lengths            |
| Euclidean distance | ‖a − b‖                 | straight-line distance                       |

To decide how similar two sentence embeddings are, cosine similarity is the
usual choice: it is the cosine of the angle between the two vectors, 1 when they
point the same way and 0 when they are orthogonal. The length of an embedding
tends to reflect incidental things such as how long or how frequent the text is,
while its direction carries the meaning, so a measure that ignores length is
what we want.

If every vector is normalised to unit length, the three metrics produce the same
ranking: cosine equals the dot product, and ‖a − b‖² = 2 − 2·(a·b). Many systems
normalise once at indexing time and then use the plain dot product, which is
cheaper.

## Exact search

The baseline is brute force: compare the query with every stored vector and keep
the best k. That is O(n · d) per query, embarrassingly parallel, and with SIMD it
is faster than most people expect. A million 384-dimensional vectors can be
scanned in tens of milliseconds on one core. Below roughly 100,000 vectors there
is rarely a reason to do anything more complicated, and brute force has perfect
recall.

```python
import numpy as np

# rows of `corpus` and `query` are unit length, so dot product == cosine
scores = corpus @ query                    # shape (n,)
top_k = np.argpartition(-scores, 10)[:10]  # unordered ten best
top_k = top_k[np.argsort(-scores[top_k])]  # now best first
```

## Approximate nearest neighbour (ANN) indexes

Beyond a few million vectors an exact scan is too slow, and we accept answers
that are *nearly* right in exchange for speed.

### HNSW

Hierarchical Navigable Small World graphs. Every vector is a node connected to
its M nearest neighbours. Nodes are also placed in a hierarchy of layers, where
each upper layer holds an exponentially smaller sample of the nodes, in the
manner of a skip list. A search enters at the top layer, greedily moves to
whichever neighbour is closest to the query, drops down a layer when it can make
no further progress, and finishes with a wider beam search on the bottom layer.

- Query time grows roughly logarithmically with the number of vectors.
- `M` and `efConstruction` control build quality; `efSearch` trades recall for
  speed at query time.
- Costs: memory (the graph is stored alongside the vectors), slow builds, and
  awkward deletes.

### IVF

An inverted file index clusters the vectors with k-means into `nlist` cells. At
query time only the `nprobe` cells whose centroids are closest to the query are
scanned. It is simple and compact; recall suffers when the true neighbour sits
just across a cell boundary, which `nprobe` compensates for.

### Product quantisation

PQ compresses the vectors themselves. Each vector is split into m sub-vectors
and each sub-vector is replaced by the id of its nearest centroid in a small
codebook, so a 1536-byte vector can shrink to 32 bytes. Distances are then
estimated from lookup tables. PQ is usually combined with IVF, and the top
candidates are re-ranked using the original vectors.

### Recall versus latency

Every ANN index is a dial between two numbers:

- **recall@k**: the fraction of the true k nearest neighbours that the index
  returns;
- **latency**, or queries per second.

Always measure both against brute-force ground truth on your own data. A
benchmark that reports speed without recall is meaningless.

## Why hybrid search

Dense vectors and keyword search fail in opposite ways.

| query                              | keyword search (BM25)       | vector search                     |
|------------------------------------|-----------------------------|-----------------------------------|
| `ERR_CONN_RESET 0x80072EFE`        | exact match, excellent      | poor: rare tokens embed badly     |
| "my laptop gets hot when idle"     | misses "thermal throttling" | finds it: same meaning            |
| a function name such as `parse_hdr`| excellent                   | unreliable                        |
| a paraphrased question             | weak                        | strong                            |

Combining keyword search with vector search covers both cases. The practical
difficulty is that the two scores are on unrelated scales: a BM25 score is
unbounded and depends on the corpus, while a cosine similarity lies between −1
and 1. **Reciprocal rank fusion** avoids the issue by ignoring the scores and
combining only the *ranks* each system assigns. Lexical-plus-dense fusion is a
strong baseline that is hard to beat without a learned re-ranker.

## Chunking

Retrieval quality depends heavily on how documents are split before embedding:

- Chunks that are too large blur several topics into one vector and overflow the
  model's input limit.
- Chunks that are too small lose the context needed to interpret them.
- Splitting on structure (headings, paragraphs) beats splitting at fixed
  character offsets, and a small overlap protects sentences that straddle a
  boundary.
- Prepending the section title to each chunk helps a paragraph that never
  repeats its own subject.

## Evaluating a retriever

Build a small set of queries with known relevant documents, then report
recall@k (did the right document appear at all?), MRR (how high was the first
correct result?) and nDCG (were the best results ranked highest?). Twenty
carefully chosen queries are enough to catch regressions; they are not enough to
claim state of the art.
