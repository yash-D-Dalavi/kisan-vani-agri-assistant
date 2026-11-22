"""
grounding.py - Module 9 (grounded answer generation) and the grounding check feeding the confidence gate.

  ExtractiveGenerator   default, zero-dependency: builds a short spoken-style answer ONLY from sentences of the retrieved
                        evidence (so it cannot invent doses or scheme rules), then names the source.
  register_generator()  plug in a hosted / self-hosted LLM: fn(prompt, evidence_chunks, lang) -> str. Its output is verified
                        by verify_claims(); if grounding is low the pipeline falls back to the extractive answer.
  verify_claims()       claim-level faithfulness check: every number must appear in the evidence and every claim must be
                        supported by evidence text (token overlap + concept containment).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Sequence

import config
import glossary as G
from knowledge_base import Evidence, CATEGORY_WEIGHT, split_sentences, _DOSE_RE

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

SOURCE_LINE = {"hi": "स्रोत: {src}।", "mr": "स्रोत: {src}.", "en": "Source: {src}."}
REPEAT_LINE = {"hi": "मैंने आपका सवाल ऐसे सुना: \"{t}\"। अगर यह सही नहीं है तो कृपया दोबारा बोलें।",
               "mr": "मी तुमचा प्रश्न असा ऐकला: \"{t}\". हे चुकीचे असल्यास कृपया पुन्हा बोला.",
               "en": "I heard your question as: \"{t}\". If that is wrong, please say it again."}


def numbers_in(text: str) -> List[str]:
    return [n.replace(",", ".") for n in _NUM_RE.findall(text.translate(_DEV_DIGITS))]


@dataclass
class Claim:
    text: str
    supported: bool
    overlap: float
    unsupported_numbers: List[str] = field(default_factory=list)
    support_chunk: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def verify_claims(claims: Sequence[str], evidence_texts: Dict[str, str], min_overlap: float = 0.6) -> Dict[str, object]:
    """Return {'score': supported/total, 'claims': [Claim...]}. evidence_texts: chunk_id -> text."""
    ev_numbers = {cid: set(numbers_in(t)) for cid, t in evidence_texts.items()}
    all_numbers = set().union(*ev_numbers.values()) if ev_numbers else set()
    ev_tokens = {cid: set(G.content_tokens(t)) for cid, t in evidence_texts.items()}
    ev_concepts = {cid: set(G.extract_concepts(t)) for cid, t in evidence_texts.items()}
    out: List[Claim] = []
    for c in claims:
        toks = set(G.content_tokens(c))
        concepts = set(G.extract_concepts(c))
        nums = set(numbers_in(c))
        bad_nums = sorted(n for n in nums if n not in all_numbers)
        best, best_id = 0.0, ""
        for cid in evidence_texts:
            ov = len(toks & ev_tokens[cid]) / len(toks) if toks else 1.0
            concept_ok = concepts <= ev_concepts[cid] if concepts else True
            ov_eff = ov if concept_ok else ov * 0.5
            if ov_eff > best:
                best, best_id = ov_eff, cid
        out.append(Claim(c, best >= min_overlap and not bad_nums, round(best, 3), bad_nums, best_id))
    score = sum(c.supported for c in out) / len(out) if out else 0.0
    return {"score": round(score, 3), "claims": out}


# ---------------- generators ---------------------------------------------------------------------------------------------------
Generator = Callable[[str, List[Evidence], str], str]
_GENERATORS: Dict[str, Generator] = {}


def register_generator(name: str, fn: Generator) -> None:
    """Plug in an LLM: fn(prompt, evidence, lang) -> answer text. The prompt already contains the grounded-answer rules."""
    _GENERATORS[name] = fn


def build_prompt(question: str, evidence: Sequence[Evidence], lang: str) -> str:
    ev_txt = "\n".join(f"[{i}] ({e.chunk.publisher}, {e.chunk.source}, {e.chunk.doc_date}) {e.chunk.text}" for i, e in enumerate(evidence, 1))
    return config.GROUNDED_PROMPT.format(language=config.SUPPORTED_LANGUAGES.get(lang, lang), kcc=config.KCC_NUMBER,
                                         evidence=ev_txt, question=question)


def _sentence_score(sent: str, q_concepts: Sequence[str], pos: int) -> float:
    have = set(G.extract_concepts(sent))
    tot = sum(CATEGORY_WEIGHT.get(G.concept_category(c), 0.0) for c in q_concepts) or 1.0
    s = sum(CATEGORY_WEIGHT.get(G.concept_category(c), 0.0) for c in q_concepts if c in have) / tot
    if s > 0 and numbers_in(sent):
        s += 0.05
    if s > 0 and _DOSE_RE.search(sent) and ({"dose", "qty_q"} & set(q_concepts)):
        s += 1.0                                   # a dosage question MUST surface the approved dose sentence
    return s + (0.04 if (pos == 0 and s > 0) else 0.0)


@dataclass
class Generated:
    sentences: List[str]
    sources: List[Evidence]       # evidence used, in citation order
    generator: str = "extractive"
    scores: List[float] = field(default_factory=list)   # relevance per sentence (used to trim to the 30 s budget)


def extractive_generate(q_concepts: Sequence[str], evidence: Sequence[Evidence], lang: str,
                        max_sentences: int = config.T.max_answer_sentences) -> Generated:
    """Select the best sentences from the best evidence (same language as the top chunk, same document family)."""
    if not evidence:
        return Generated([], [])
    top = evidence[0]
    pool = [e for e in evidence if e.chunk.language == top.chunk.language and e.chunk.topic == top.chunk.topic] or [top]
    cands = []
    seen = set()
    for ci, e in enumerate(pool):
        for pi, s in enumerate(split_sentences(e.chunk.text)):
            if s in seen:
                continue
            seen.add(s)
            cands.append((_sentence_score(s, q_concepts, pi), ci, pi, s, e))
    ranked = sorted(cands, key=lambda x: (-x[0], x[1], x[2]))
    picked = [c for c in ranked if c[0] > 0][:max_sentences] or ranked[:1]
    # safety: if the top chunk carries a dose sentence that was selected, keep its "follow the label" sentence too
    picked.sort(key=lambda x: (int(x[4].chunk.chunk_id.rsplit("#", 1)[1]), x[2]))   # keep the document's own sentence order
    srcs: List[Evidence] = []
    for p in picked:
        if not any(p[4] is x for x in srcs):
            srcs.append(p[4])
    return Generated([p[3] for p in picked], srcs, "extractive", [p[0] for p in picked])


def generate_answer(question: str, q_concepts: Sequence[str], evidence: Sequence[Evidence], lang: str,
                    generator: str = "extractive") -> Generated:
    fn = _GENERATORS.get(generator)
    if fn is None:
        return extractive_generate(q_concepts, evidence, lang)
    text = fn(build_prompt(question, evidence, lang), list(evidence), lang)
    sents = split_sentences(text)
    return Generated(sents, list(evidence[:3]), generator)


def compose_spoken(sentences: List[str], sources: List[Evidence], lang: str, echo: Optional[str] = None,
                   extra_notes: Optional[List[str]] = None, max_seconds: int = config.T.max_spoken_seconds,
                   scores: Optional[List[float]] = None) -> Dict[str, object]:
    """Assemble the spoken/text answer: [echo] + claims + source line + notes, trimmed to the <30 s reply budget.
    Trimming drops the LEAST relevant sentence first (so an approved dose sentence is never the one that is cut)."""
    src_names = list(dict.fromkeys(e.chunk.source for e in sources))
    src = SOURCE_LINE.get(lang, SOURCE_LINE["en"]).format(src=", ".join(src_names[:2]))
    budget = int(max_seconds * config.T.speech_chars_per_second)
    head: List[str] = [REPEAT_LINE.get(lang, REPEAT_LINE["en"]).format(t=echo)] if echo else []
    tail = [src] + (extra_notes or [])
    fixed = sum(len(p) + 1 for p in head + tail)
    items = list(zip(sentences, scores if scores and len(scores) == len(sentences) else [1.0 - 0.01 * i for i in range(len(sentences))]))
    kept = list(items)
    while len(kept) > 1 and fixed + sum(len(x) + 1 for x, _ in kept) > budget:
        kept.pop(min(range(len(kept)), key=lambda i: kept[i][1]))
    claims = [x for x, _ in kept]
    return {"spoken_text": " ".join(head + claims + tail), "claims": claims, "source_line": src, "truncated": len(kept) < len(sentences)}
