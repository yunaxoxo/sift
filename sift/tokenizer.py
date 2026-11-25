"""The single tokenizer used for both indexing and querying.

BM25 only works if documents and queries are tokenized identically, so every
caller goes through :func:`tokenize`. The rule is deliberately simple:

    casefold, then take maximal runs of word characters (letters, digits, ``_``)

Changing this file changes every BM25 score: re-index afterwards.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    """Split ``text`` into lowercase word tokens.

    >>> tokenize("Page-fault handling: the TLB & CR3!")
    ['page', 'fault', 'handling', 'the', 'tlb', 'cr3']
    """
    return _WORD.findall(text.casefold())
