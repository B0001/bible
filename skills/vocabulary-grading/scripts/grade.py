#!/usr/bin/env python3
"""Reference implementation of the four core vocabulary-grading functions.

Stdlib only, no dependencies — copy into any project. Tokenization is
deliberately naive here (lowercased \\w+); swap in a real language-aware
tokenizer (see the multilingual-text-tokenization skill) and pass the same
one over both the corpus and the vocabulary.
"""

import math
import re
from collections import Counter

TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text):
    """Placeholder tokenizer. Replace with a language-aware one."""
    return TOKEN_RE.findall(text.lower())


def comprehension_rate(text, vocab_forms, min_length=1):
    """Fraction of this item's tokens the reader already knows.

    Repeats count with multiplicity. Items shorter than ``min_length`` score
    0.0. The empty guard is separate so ``min_length=0`` cannot cause 0/0.
    """
    forms = tokenize(text)
    if not forms or len(forms) < min_length:
        return 0.0
    return sum(1 for f in forms if f in vocab_forms) / len(forms)


def corpus_ranks(token_lists):
    """Rank every form by frequency within this corpus. 1 = most frequent.

    Ties break lexicographically, which makes the output deterministic and
    reproducible across runs and across reimplementations.
    """
    counts = Counter(t for toks in token_lists for t in toks)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {form: i + 1 for i, (form, _) in enumerate(ordered)}


def verse_difficulty(forms, ranks, target=0.95, known=frozenset()):
    """Vocabulary size N at which >= ``target`` of ``forms`` is known.

    Already-known forms are free (effective rank 0); forms absent from the
    corpus fall back past the end. Returns None for an empty item.
    """
    if not forms:
        return None
    fallback = len(ranks) + 1
    eff = sorted(0 if f in known else ranks.get(f, fallback) for f in forms)
    k = max(1, math.ceil(target * len(eff)))
    return eff[k - 1]


def longest_span(known, total, min_rate):
    """Longest contiguous [i, j) whose COMBINED rate is >= ``min_rate``.

    Reduction: with a[k] = known[k] - min_rate * total[k] and P its prefix
    sum, the rate condition is exactly P[j] - P[i] >= 0. Only prefix-minimum
    left endpoints can be optimal, so one forward pass builds a decreasing
    candidate stack and one backward pass consumes it. O(n).

    Returns (i, j) or None.
    """
    n = len(known)
    prefix = [0.0] * (n + 1)
    for i in range(n):
        prefix[i + 1] = prefix[i] + known[i] - min_rate * total[i]

    stack = []
    for i in range(n + 1):
        if not stack or prefix[i] < prefix[stack[-1]]:
            stack.append(i)

    best_len = best_i = best_j = 0
    j = n
    while j >= 0 and stack:
        while stack and prefix[j] >= prefix[stack[-1]]:
            if j - stack[-1] > best_len:
                best_len, best_i, best_j = j - stack[-1], stack[-1], j
            stack.pop()
        j -= 1

    return (best_i, best_j) if best_len > 0 else None


def next_words_to_learn(token_lists, vocab_forms, ranks=None,
                        target=0.95, min_length=1, top_n=20):
    """Rank forms by how many unreadable items each would unlock alone.

    All occurrences of a candidate are credited at once. Items needing two
    new words score for neither. Ties break by corpus rank ascending when
    ``ranks`` is supplied, so the more frequent word wins.
    """
    unlocks = Counter()
    for forms in token_lists:
        total = len(forms)
        if total < min_length or total == 0:
            continue
        counts = Counter(forms)
        known = sum(c for f, c in counts.items() if f in vocab_forms)
        if known / total >= target:
            continue
        for form, count in counts.items():
            if form not in vocab_forms and (known + count) / total >= target:
                unlocks[form] += 1

    fallback = len(ranks) + 1 if ranks else 0
    def key(item):
        form, count = item
        return (-count, ranks.get(form, fallback) if ranks else form)

    return sorted(unlocks.items(), key=key)[:top_n]


if __name__ == "__main__":
    # The worked example from SKILL.md, usable as a test fixture.
    docs = [["a", "b", "a"], ["b", "c"], ["a", "c", "d", "a"]]
    ranks = corpus_ranks(docs)
    assert ranks == {"a": 1, "b": 2, "c": 3, "d": 4}, ranks
    assert [verse_difficulty(d, ranks) for d in docs] == [2, 3, 4]
    assert longest_span([2, 0, 2, 2], [2, 3, 2, 2], 0.95) == (2, 4)
    print("ok:", ranks)
