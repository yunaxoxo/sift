"""Local embedding model loaders.

Everything here runs on your machine: no text leaves the process. Three kinds,
selected with ``sift index --embedder``:

``sentence-transformers``
    A real model through PyTorch. Default model: ``all-MiniLM-L6-v2``
    (384 dimensions, ~90 MB, downloaded once into the Hugging Face cache).
    Install with ``pip install -e '.[embeddings]'``.

``onnx``
    The same models through ONNX Runtime: smaller install, faster cold start.
    Install with ``pip install -e '.[onnx]'``.

``hash``
    A deterministic feature-hashing bag of words. No download, no semantics.
    It exists so tests and CI can exercise the whole pipeline in milliseconds.
    **Never quote benchmark numbers produced with it.**

Loading is lazy. Constructing an embedder is free; the heavy import and the
weights are loaded by :meth:`Embedder.load` (or implicitly by the first
``encode``). Consequences worth having:

* ``sift stats`` and ``sift search --mode lexical`` never import PyTorch.
* ``sift index`` on an unchanged directory finishes without loading a model.
* Model load time is measured on its own (``load_ms``) instead of polluting the
  embedding or query latency numbers.

The kind and model used at index time are stored in the database; ``sift
search`` reloads the same one automatically, because vectors produced by two
different models are not comparable.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from sift import config
from sift.errors import SiftError
from sift.tokenizer import tokenize


class Embedder(Protocol):
    """What the indexer and the search pipeline need from an embedding model."""

    kind: str
    """One of :data:`sift.config.EMBEDDER_KINDS`."""
    model: str
    """Model identifier, stored in the index metadata."""
    load_ms: float | None
    """How long :meth:`load` took, or ``None`` if the model is not loaded yet."""

    @property
    def dim(self) -> int:
        """Length of every vector returned by :meth:`encode` (loads the model)."""
        ...

    def load(self) -> None:
        """Load the model if it is not loaded yet. Safe to call repeatedly."""
        ...

    def encode(self, texts: Sequence[str]) -> npt.NDArray[np.float32]:
        """Embed ``texts``; returns an array of shape ``(len(texts), dim)``."""
        ...


class SentenceTransformerEmbedder:
    """A sentence-transformers model on the PyTorch or ONNX Runtime backend.

    Vectors are returned exactly as the model produces them, *not*
    L2-normalised.
    """

    def __init__(
        self,
        model: str = config.DEFAULT_MODEL,
        *,
        kind: str = "sentence-transformers",
        batch_size: int = config.DEFAULT_BATCH_SIZE,
        offline: bool | None = None,
    ) -> None:
        self.kind = kind
        self.model = model
        self.load_ms: float | None = None
        self._batch_size = batch_size
        self._offline = config.offline_from_env() if offline is None else offline
        self._model: Any = None
        self._dim = 0

    @property
    def dim(self) -> int:
        self.load()
        return self._dim

    def load(self) -> None:
        if self._model is not None:
            return
        start = time.perf_counter_ns()

        # The Rust `tokenizers` library warns (and can deadlock) if the process
        # forks after it started its thread pool. We never need that pool.
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        extra = "onnx" if self.kind == "onnx" else "embeddings"
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise SiftError(
                f"the '{self.kind}' embedder needs sentence-transformers, "
                "which is not installed.\n"
                f"  Install it:  pip install -e '.[{extra}]'\n"
                "  Or use the offline test embedder:  sift index <dir> --embedder hash"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self.kind == "onnx":
            # Passed only when needed: the `backend` argument does not exist
            # before sentence-transformers 3.2.
            kwargs["backend"] = "onnx"

        # Local first: try the on-disk cache without touching the network.
        # Only a model that has never been downloaded falls through to the
        # Hugging Face Hub, and SIFT_OFFLINE=1 forbids even that.
        model = None
        try:
            model = SentenceTransformer(self.model, local_files_only=True, **kwargs)
        except Exception:
            if self._offline:
                raise SiftError(
                    f"embedding model {self.model!r} is not in the local cache and "
                    "SIFT_OFFLINE is set. Unset it once to download the model."
                ) from None
        try:
            if model is None:
                model = SentenceTransformer(self.model, **kwargs)
        except (OSError, ImportError, ValueError) as exc:
            raise SiftError(
                f"could not load embedding model {self.model!r}: {exc}\n"
                f"  If a dependency is missing:  pip install -e '.[{extra}]'"
            ) from exc

        # Probing with a real encode call is version-proof (the accessor for
        # the dimension has been renamed across releases) and warms the model,
        # so the first real batch is not penalised by lazy initialisation.
        probe = self._encode(model, ["dimension probe"])
        self._dim = int(probe.shape[1])
        self._model = model
        self.load_ms = (time.perf_counter_ns() - start) / 1e6

    def _encode(self, model: Any, texts: Sequence[str]) -> npt.NDArray[np.float32]:
        vectors = model.encode(
            list(texts),
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def encode(self, texts: Sequence[str]) -> npt.NDArray[np.float32]:
        self.load()
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        return self._encode(self._model, texts)


class HashingEmbedder:
    """Deterministic, dependency-free stand-in for a real model (tests and CI).

    Feature hashing: each token is hashed to one of ``dim`` buckets with a
    pseudo-random sign, bucket values are summed, and the vector is scaled to
    unit length. Two texts end up close only if they share words, so this is a
    second lexical signal, not a semantic one.

    ``hashlib.blake2b`` is used instead of built-in ``hash()`` because the
    latter is salted per process and would make indexes non-reproducible.
    """

    kind = "hash"

    def __init__(self, dim: int = config.HASH_EMBEDDING_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self._dim = dim
        self.model = f"feature-hash-{dim}"
        self.load_ms: float | None = None

    @property
    def dim(self) -> int:
        return self._dim

    def load(self) -> None:
        if self.load_ms is None:
            self.load_ms = 0.0

    def encode(self, texts: Sequence[str]) -> npt.NDArray[np.float32]:
        self.load()
        vectors = np.zeros((len(texts), self._dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in tokenize(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "little")
                sign = 1.0 if value >> 63 == 0 else -1.0
                vectors[row, value % self._dim] += sign
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return vectors / norms


def make_embedder(
    kind: str,
    model: str | None = None,
    *,
    batch_size: int = config.DEFAULT_BATCH_SIZE,
) -> Embedder:
    """Create (but do not load) the embedder for ``kind``.

    ``model`` is ignored for ``hash`` except to recover its dimension from a
    stored name such as ``feature-hash-256``.
    """
    if kind == "hash":
        dim = config.HASH_EMBEDDING_DIM
        if model and model.startswith("feature-hash-"):
            dim = int(model.removeprefix("feature-hash-"))
        return HashingEmbedder(dim)
    if kind in ("sentence-transformers", "onnx"):
        return SentenceTransformerEmbedder(
            model or config.DEFAULT_MODEL, kind=kind, batch_size=batch_size
        )
    raise SiftError(
        f"unknown embedder kind {kind!r}; expected one of {', '.join(config.EMBEDDER_KINDS)}"
    )
