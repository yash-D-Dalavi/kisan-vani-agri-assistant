"""
eval_metrics.py - metric implementations used by evaluator.py (blueprint Module 13 / section 2).

  wer / cer            word & character error rate (uses `jiwer` when installed, otherwise a built-in Levenshtein)
  chrf / bleu          character n-gram F-score (chrF, beta=2) and smoothed sentence BLEU  (COMET needs a model: see README)
  recall_at_k / mrr    retrieval metrics
  percentile           latency percentiles
All implemented with the standard library so the evaluation harness never fails on a missing dependency.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Iterable, List, Optional, Sequence

import glossary as G


def _edit_distance(a: Sequence, b: Sequence) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _norm_words(s: str) -> List[str]:
    return G.tokenize(s)


def wer(refs: Sequence[str], hyps: Sequence[str]) -> float:
    """Corpus-level WER = total edits / total reference words."""
    try:
        import jiwer  # type: ignore
        return float(jiwer.wer([" ".join(_norm_words(r)) for r in refs], [" ".join(_norm_words(h)) or "-" for h in hyps]))
    except Exception:  # noqa: BLE001 - ImportError or any jiwer edge case -> built-in implementation
        edits = sum(_edit_distance(_norm_words(r), _norm_words(h)) for r, h in zip(refs, hyps))
        words = sum(len(_norm_words(r)) for r in refs)
        return edits / words if words else 0.0


def cer(refs: Sequence[str], hyps: Sequence[str]) -> float:
    edits = sum(_edit_distance(list(G.normalize(r).replace(" ", "")), list(G.normalize(h).replace(" ", ""))) for r, h in zip(refs, hyps))
    chars = sum(len(G.normalize(r).replace(" ", "")) for r in refs)
    return edits / chars if chars else 0.0


def _char_ngrams(s: str, n: int) -> Counter:
    s = G.normalize(s).replace(" ", "")
    return Counter(s[i:i + n] for i in range(max(0, len(s) - n + 1)))


def chrf(hyp: str, ref: str, max_n: int = 6, beta: float = 2.0) -> float:
    """chrF (character n-gram F-beta), range 0..100."""
    precs, recs = [], []
    for n in range(1, max_n + 1):
        h, r = _char_ngrams(hyp, n), _char_ngrams(ref, n)
        if not h or not r:
            continue
        overlap = sum((h & r).values())
        precs.append(overlap / sum(h.values()))
        recs.append(overlap / sum(r.values()))
    if not precs:
        return 0.0
    p, r = sum(precs) / len(precs), sum(recs) / len(recs)
    if p + r == 0:
        return 0.0
    return 100 * (1 + beta ** 2) * p * r / (beta ** 2 * p + r)


def bleu(hyp: str, ref: str, max_n: int = 4) -> float:
    """Smoothed (add-1) sentence BLEU, range 0..100."""
    h, r = _norm_words(hyp), _norm_words(ref)
    if not h or not r:
        return 0.0
    logs = 0.0
    for n in range(1, max_n + 1):
        hn = Counter(tuple(h[i:i + n]) for i in range(len(h) - n + 1))
        rn = Counter(tuple(r[i:i + n]) for i in range(len(r) - n + 1))
        overlap = sum((hn & rn).values())
        total = max(1, sum(hn.values()))
        logs += math.log((overlap + 1) / (total + 1))
    bp = 1.0 if len(h) >= len(r) else math.exp(1 - len(r) / len(h))
    return 100 * bp * math.exp(logs / max_n)


def recall_at_k(ranked_topics: Sequence[str], gold: Iterable[str], k: int = 5) -> float:
    gold = set(gold)
    return 1.0 if gold & set(ranked_topics[:k]) else 0.0


def reciprocal_rank(ranked_topics: Sequence[str], gold: Iterable[str]) -> float:
    gold = set(gold)
    for i, t in enumerate(ranked_topics, 1):
        if t in gold:
            return 1.0 / i
    return 0.0


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    return s[int(k)] if f == c else s[f] + (s[c] - s[f]) * (k - f)
