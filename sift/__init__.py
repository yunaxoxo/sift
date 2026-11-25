"""sift: local-first hybrid search for Markdown notes and codebases.

Pipeline::

    index:   Rust walk + chunk  ->  embed  ->  SQLite
    search:  BM25 (lexical)  +  cosine (semantic)  ->  Reciprocal Rank Fusion

Module map:

==================  ==========================================================
``cli``             argparse entry points: ``index``, ``search``, ``stats``
``indexer``         index pipeline (incremental, per-stage timings)
``search``          query pipeline (calls into ``ranking``)
``ranking``         BM25, cosine similarity, RRF  (**hand-written, see stubs**)
``chunking``        bridge to the Rust extension ``sift._rust_core``
``embeddings``      local embedding model loaders
``db``              SQLite schema and queries
``telemetry``       stage timers and latency percentiles
``tokenizer``       the one tokenizer shared by indexing and querying
==================  ==========================================================
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sift")
except PackageNotFoundError:  # running from a checkout that was never installed
    __version__ = "0.0.0+uninstalled"

__all__ = ["__version__"]
