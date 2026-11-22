"""
knowledge_base.py - Modules 5, 6, 7 of the blueprint.

  * Documents with full metadata: source, publisher, crop, region/state, season, language, doc_date, version, license, approved
  * Structure-aware sentence chunking (with 1-sentence overlap), title-aware indexing
  * Duplicate detection (exact hash + near-duplicate shingle Jaccard) and document versioning (newer version supersedes older)
  * Approval flag: ONLY approved + active documents are ever retrievable
  * Hybrid retrieval: dense vectors + BM25 -> Reciprocal Rank Fusion -> reranker
  * Metadata-aware filtering by crop / region (a cotton question never returns tomato advice)
  * Pluggable embedder / reranker: hashing-concept embedder (zero-dependency default) or BGE-M3 / cross-encoder if installed

Cross-lingual matching: queries and chunks are both mapped to glossary *concept ids* (see glossary.py), so a Marathi
question can retrieve a Hindi or English passage even with the zero-dependency embedder.  With BGE-M3 the same
interface gives true semantic multilingual retrieval.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

import config
import glossary as G
from seed_data import SEED_DOCUMENTS

REQUIRED_META = ("source", "publisher", "license", "language", "doc_date", "text")
STATE_FILE = config.DATA_DIR / "kb_state.json"

CATEGORY_WEIGHT = {"crop": 3.0, "pest": 3.0, "chemical": 3.0, "chemical_other": 3.0, "scheme": 2.5, "practice": 2.0,
                   "symptom": 1.5, "dose": 1.5, "market": 1.0, "weather": 1.0, "season": 1.0, "helpline": 3.0, "generic": 0.5}
_DOSE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:ग्राम|ग्रॅम|मिली|ml|g\b|gram|grams|किलो|kg|लीटर|लिटर|litre|liter)", re.I)
_SENT_SPLIT = re.compile(r"(?<=[।\.\?!])\s+")


# ----------------------------------------------------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class Document:
    doc_id: str
    topic: str
    title: str
    text: str
    source: str
    publisher: str
    language: str
    doc_date: str
    license: str
    crop: str = "all"
    region: str = "India"
    season: str = "all"
    version: int = 1
    approved: bool = False
    safety_level: str = "normal"      # normal | pest | dose
    source_url: str = ""
    active: bool = True               # False once superseded by a newer version
    superseded_by: str = ""
    content_hash: str = ""
    origin: str = "seed"              # seed | admin
    ingested_at: str = ""

    @property
    def family(self) -> str:
        return f"{self.topic}-{self.language}"

    def review_due(self, today: Optional[date] = None) -> bool:
        """Outdated-knowledge mitigation: flag documents older than ~18 months for scheduled expert review."""
        try:
            d = datetime.strptime(self.doc_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return True
        return ((today or date.today()) - d).days > 540

    def to_dict(self, with_text: bool = True) -> dict:
        d = asdict(self)
        d["family"] = self.family
        d["review_due"] = self.review_due()
        d["retrievable"] = self.approved and self.active
        if not with_text:
            d["text"] = d["text"][:160] + ("..." if len(d["text"]) > 160 else "")
        return d


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    topic: str
    title: str
    text: str
    language: str
    crop: str
    region: str
    season: str
    source: str
    publisher: str
    version: int
    doc_date: str
    safety_level: str
    source_url: str
    concepts: frozenset = frozenset()
    tokens: Tuple[str, ...] = field(default=(), repr=False)
    has_dose: bool = False
    vec: Tuple[float, ...] = field(default=(), repr=False)

    def cite(self) -> dict:
        return {"chunk_id": self.chunk_id, "doc_id": self.doc_id, "topic": self.topic, "title": self.title,
                "source": self.source, "publisher": self.publisher, "doc_date": self.doc_date, "version": self.version,
                "language": self.language, "crop": self.crop, "region": self.region, "url": self.source_url,
                "snippet": self.text[:220] + ("..." if len(self.text) > 220 else "")}


@dataclass
class Evidence:
    chunk: Chunk
    score: float                 # final reranker score 0..1
    dense: float = 0.0
    bm25: float = 0.0
    rrf: float = 0.0
    coverage: float = 0.0
    rank: int = 0

    def to_dict(self) -> dict:
        return {"chunk_id": self.chunk.chunk_id, "doc_id": self.chunk.doc_id, "topic": self.chunk.topic,
                "language": self.chunk.language, "score": round(self.score, 3), "dense": round(self.dense, 3),
                "bm25": round(self.bm25, 3), "rrf": round(self.rrf, 4), "coverage": round(self.coverage, 3), "rank": self.rank}


@dataclass
class IngestResult:
    status: str                  # added | duplicate | rejected | archived_older_version
    doc_id: str = ""
    message: str = ""
    superseded: List[str] = field(default_factory=list)
    chunks: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------------------------------------------------
# Embedders & rerankers (pluggable)
# ----------------------------------------------------------------------------------------------------------------------
class HashingConceptEmbedder:
    """Zero-dependency 'semantic' embedder: hashed bag of glossary concepts (weight 2) + stemmed tokens + token bigrams.
    Concept ids are language independent, which is what gives cross-lingual retrieval offline."""
    name = "hashing-concept-v1"

    def __init__(self, dim: int = 768):
        self.dim = dim

    def _idx(self, feat: str) -> Tuple[int, float]:
        h = int(hashlib.md5(feat.encode("utf-8")).hexdigest()[:8], 16)
        return h % self.dim, (1.0 if (h >> 31) & 1 else -1.0)

    def encode(self, text: str) -> Tuple[float, ...]:
        v = [0.0] * self.dim
        toks = G.content_tokens(text)
        feats: List[Tuple[str, float]] = [("c:" + c, 2.0) for c in G.extract_concepts(text)]
        feats += [("t:" + t, 1.0) for t in toks]
        feats += [("b:" + a + "_" + b, 0.5) for a, b in zip(toks, toks[1:])]
        for f, w in feats:
            i, s = self._idx(f)
            v[i] += s * w
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return tuple(x / n for x in v)


class SentenceTransformerEmbedder:
    """BGE-M3 / multilingual-e5 via sentence-transformers (pip install sentence-transformers)."""

    def __init__(self, model: str = "BAAI/bge-m3"):
        from sentence_transformers import SentenceTransformer  # type: ignore
        self.model = SentenceTransformer(model)
        self.name = model

    def encode(self, text: str) -> Tuple[float, ...]:
        return tuple(float(x) for x in self.model.encode(text, normalize_embeddings=True))


def get_embedder():
    if config.EMBEDDER == "bge-m3":
        try:
            return SentenceTransformerEmbedder()
        except Exception:  # noqa: BLE001 - graceful fallback
            pass
    return HashingConceptEmbedder()


def query_weight_total(concepts: Sequence[str]) -> float:
    return sum(CATEGORY_WEIGHT.get(G.concept_category(c), 0.0) for c in concepts)


def coverage_of(concepts: Sequence[str], available: frozenset) -> float:
    tot = query_weight_total(concepts)
    if tot <= 0:
        return 0.0
    got = sum(CATEGORY_WEIGHT.get(G.concept_category(c), 0.0) for c in concepts if c in available)
    return got / tot


class LexicalReranker:
    """Deterministic reranker: weighted query-concept coverage + dense + BM25 + language bonus. A multilingual
    cross-encoder (bge-reranker-v2-m3) can be swapped in through CrossEncoderReranker with the same interface."""
    name = "lexical-coverage-v1"

    def score(self, q_concepts: Sequence[str], q_lang: str, ev: Evidence) -> float:
        cov = coverage_of(q_concepts, ev.chunk.concepts)
        ev.coverage = cov
        dense_n = min(1.0, max(0.0, ev.dense) / 0.5)
        s = 0.55 * cov + 0.25 * dense_n + 0.20 * min(1.0, ev.bm25)
        if ev.chunk.language == q_lang:
            s += 0.04
        return max(0.0, min(1.0, s))


class CrossEncoderReranker:
    name = "cross-encoder"

    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3"):
        from sentence_transformers import CrossEncoder  # type: ignore
        self.model = CrossEncoder(model)
        self.fallback = LexicalReranker()

    def score(self, q_concepts, q_lang, ev):  # pragma: no cover - needs model download
        self.fallback.score(q_concepts, q_lang, ev)  # keeps coverage populated
        return float(1 / (1 + math.exp(-self.model.predict([(" ".join(q_concepts), ev.chunk.text)])[0])))


def get_reranker():
    if config.RERANKER == "cross-encoder":
        try:
            return CrossEncoderReranker()
        except Exception:  # noqa: BLE001
            pass
    return LexicalReranker()


# ----------------------------------------------------------------------------------------------------------------------
# Chunking
# ----------------------------------------------------------------------------------------------------------------------
def split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def chunk_text(text: str, max_chars: int = config.T.chunk_chars, overlap: int = config.T.chunk_overlap_sentences) -> List[str]:
    sents = split_sentences(text)
    chunks: List[List[str]] = []
    cur: List[str] = []
    for s in sents:
        if cur and sum(len(x) + 1 for x in cur) + len(s) > max_chars:
            chunks.append(cur)
            cur = cur[-overlap:] if overlap else []
        cur.append(s)
    if cur and (not chunks or cur != chunks[-1][-len(cur):]):
        chunks.append(cur)
    return [" ".join(c) for c in chunks]


def _shingles(text: str, n: int = 4) -> set:
    t = G.tokenize(text)
    return {" ".join(t[i:i + n]) for i in range(max(1, len(t) - n + 1))}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


# ----------------------------------------------------------------------------------------------------------------------
# Knowledge base
# ----------------------------------------------------------------------------------------------------------------------
class KnowledgeBase:
    def __init__(self, embedder=None, reranker=None, load_seed: bool = True, state_path=STATE_FILE, persist: bool = True):
        self.embedder = embedder or get_embedder()
        self.reranker = reranker or get_reranker()
        self.docs: Dict[str, Document] = {}
        self.chunks: List[Chunk] = []
        self._lock = threading.RLock()
        self._state_path = state_path
        self._persist = persist
        self._idf: Dict[str, float] = {}
        self._avgdl = 1.0
        self._bm25_tf: Dict[str, Dict[str, int]] = {}
        self.embedding_version = self.embedder.name
        if load_seed:
            for d in SEED_DOCUMENTS:
                self._ingest(dict(d), origin="seed", save=False)
            self._load_state()
        self._rebuild_index()

    # ---------------- ingestion ----------------
    def add_document(self, raw: dict, approve: Optional[bool] = None) -> IngestResult:
        """Admin ingestion: validate metadata, detect duplicates, apply versioning. New docs are UNAPPROVED by default."""
        with self._lock:
            res = self._ingest(dict(raw), origin="admin", save=True, approve=approve)
            self._rebuild_index()
            return res

    def _ingest(self, raw: dict, origin: str, save: bool, approve: Optional[bool] = None) -> IngestResult:
        missing = [k for k in REQUIRED_META if not str(raw.get(k, "")).strip()]
        if missing:
            return IngestResult("rejected", raw.get("doc_id", ""), "missing required metadata: " + ", ".join(missing))
        if raw["language"] not in config.SUPPORTED_LANGUAGES:
            return IngestResult("rejected", raw.get("doc_id", ""), f"unsupported language {raw['language']}")
        try:
            datetime.strptime(str(raw["doc_date"])[:10], "%Y-%m-%d")
        except ValueError:
            return IngestResult("rejected", raw.get("doc_id", ""), "doc_date must be YYYY-MM-DD")
        raw.setdefault("title", raw["text"][:60])
        raw.setdefault("topic", re.sub(r"[^a-z0-9]+", "_", raw["title"].lower()).strip("_")[:40] or "misc")
        raw.setdefault("doc_id", f"{raw['topic']}_{raw['language']}_v{raw.get('version', 1)}")
        raw["version"] = int(raw.get("version", 1))
        raw["approved"] = bool(raw.get("approved", False)) if approve is None else approve
        raw.pop("family", None); raw.pop("review_due", None); raw.pop("retrievable", None)
        fields = {k: raw[k] for k in Document.__dataclass_fields__ if k in raw}
        doc = Document(**fields)
        doc.origin = origin
        doc.ingested_at = datetime.now().isoformat(timespec="seconds")
        doc.content_hash = hashlib.sha1(" ".join(G.tokenize(doc.text)).encode("utf-8")).hexdigest()
        if doc.doc_id in self.docs:
            return IngestResult("rejected", doc.doc_id, "doc_id already exists")

        active_same_family = [d for d in self.docs.values() if d.family == doc.family and d.active]
        # --- duplicate detection (skipped for a genuine newer version of the same family)
        newer_version = bool(active_same_family) and all(doc.version > d.version for d in active_same_family)
        if not newer_version:
            for d in self.docs.values():
                if not d.active or d.language != doc.language:
                    continue
                if d.content_hash == doc.content_hash or _jaccard(_shingles(d.text), _shingles(doc.text)) >= 0.85:
                    return IngestResult("duplicate", doc.doc_id, f"duplicate of {d.doc_id}")
        # --- versioning
        superseded: List[str] = []
        if active_same_family:
            if newer_version:
                for d in active_same_family:
                    d.active, d.superseded_by = False, doc.doc_id
                    superseded.append(d.doc_id)
            else:
                doc.active = False
                self.docs[doc.doc_id] = doc
                self._persist_state(save)
                return IngestResult("archived_older_version", doc.doc_id,
                                    f"version {doc.version} is not newer than the active version; stored inactive")
        self.docs[doc.doc_id] = doc
        self._persist_state(save)
        return IngestResult("added", doc.doc_id, "added" + (" (superseded: " + ", ".join(superseded) + ")" if superseded else "")
                            + ("" if doc.approved else " - awaiting approval"), superseded)

    def set_approval(self, doc_id: str, approved: bool) -> bool:
        with self._lock:
            d = self.docs.get(doc_id)
            if not d:
                return False
            d.approved = approved
            self._persist_state(True)
            self._rebuild_index()
            return True

    # ---------------- persistence (admin changes survive restarts) ----------------
    def _persist_state(self, save: bool) -> None:
        if not (save and self._persist):
            return
        try:
            state = {"approvals": {i: d.approved for i, d in self.docs.items() if d.origin == "seed"},
                     "added": [asdict(d) for d in self.docs.values() if d.origin == "admin"]}
            self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _load_state(self) -> None:
        if not (self._persist and self._state_path.exists()):
            return
        try:
            st = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for i, a in st.get("approvals", {}).items():
            if i in self.docs:
                self.docs[i].approved = a
        for raw in st.get("added", []):
            raw = {k: v for k, v in raw.items() if k in Document.__dataclass_fields__}
            if raw.get("doc_id") in self.docs:
                continue
            doc = Document(**raw)
            self.docs[doc.doc_id] = doc

    # ---------------- indexing ----------------
    def _rebuild_index(self) -> None:
        self.chunks = []
        for d in self.docs.values():
            if not (d.approved and d.active):
                continue
            crop_concept = "crop_" + d.crop if d.crop != "all" and ("crop_" + d.crop) in G.C else None
            for n, piece in enumerate(chunk_text(d.text)):
                index_text = d.title + ". " + piece          # title-aware indexing keeps split chunks on-topic
                concepts = set(G.extract_concepts(index_text))
                if crop_concept:
                    concepts.add(crop_concept)
                self.chunks.append(Chunk(
                    chunk_id=f"{d.doc_id}#{n}", doc_id=d.doc_id, topic=d.topic, title=d.title, text=piece, language=d.language,
                    crop=d.crop, region=d.region, season=d.season, source=d.source, publisher=d.publisher, version=d.version,
                    doc_date=d.doc_date, safety_level=d.safety_level, source_url=d.source_url,
                    concepts=frozenset(concepts), tokens=tuple(G.content_tokens(index_text)),
                    has_dose=bool(_DOSE_RE.search(piece)) and d.safety_level == "dose", vec=self.embedder.encode(index_text)))
        # BM25 statistics over tokens + concept ids
        self._bm25_tf, df, total = {}, {}, 0
        for c in self.chunks:
            feats = list(c.tokens) + ["c:" + x for x in c.concepts]
            tf: Dict[str, int] = {}
            for f in feats:
                tf[f] = tf.get(f, 0) + 1
            self._bm25_tf[c.chunk_id] = tf
            total += len(feats)
            for f in tf:
                df[f] = df.get(f, 0) + 1
        n = max(1, len(self.chunks))
        self._avgdl = total / n if total else 1.0
        self._idf = {f: math.log(1 + (n - v + 0.5) / (v + 0.5)) for f, v in df.items()}

    def _bm25(self, q_feats: Sequence[str], c: Chunk, k1: float = 1.5, b: float = 0.75) -> Tuple[float, float]:
        tf = self._bm25_tf[c.chunk_id]
        dl = sum(tf.values())
        score = best = 0.0
        for f in set(q_feats):
            idf = self._idf.get(f)
            if idf is None:
                continue
            best += idf * (k1 + 1)
            t = tf.get(f, 0)
            if t:
                score += idf * t * (k1 + 1) / (t + k1 * (1 - b + b * dl / self._avgdl))
        return score, (score / best if best else 0.0)

    # ---------------- retrieval ----------------
    def search(self, query: str, lang: str = "hi", crop: Optional[str] = None, region: Optional[str] = None,
               top_k: int = config.T.top_k, candidate_k: int = config.T.candidate_k, concepts: Optional[Sequence[str]] = None) -> List[Evidence]:
        """Hybrid dense + BM25 -> RRF -> rerank. Only approved, active, metadata-compatible chunks are considered."""
        q_concepts = list(concepts) if concepts is not None else G.extract_concepts(query)
        q_feats = G.content_tokens(query) + ["c:" + c for c in q_concepts]
        qv = self.embedder.encode(query)
        pool = []
        for c in self.chunks:
            if crop and c.crop not in ("all", crop):
                continue
            if region and c.region not in ("India", "all", region):
                continue
            pool.append(c)
        if not pool:
            return []
        dense = {c.chunk_id: sum(a * b for a, b in zip(qv, c.vec)) for c in pool}
        bm = {c.chunk_id: self._bm25(q_feats, c) for c in pool}
        r_dense = {cid: r for r, (cid, _) in enumerate(sorted(dense.items(), key=lambda kv: -kv[1]))}
        r_bm = {cid: r for r, (cid, _) in enumerate(sorted(bm.items(), key=lambda kv: -kv[1][0]))}
        k = config.T.rrf_k
        evs: List[Evidence] = []
        for c in pool:
            rrf = 1 / (k + r_dense[c.chunk_id] + 1) + 1 / (k + r_bm[c.chunk_id] + 1)
            evs.append(Evidence(c, 0.0, dense[c.chunk_id], bm[c.chunk_id][1], rrf))
        evs.sort(key=lambda e: -e.rrf)
        evs = evs[:candidate_k]
        for e in evs:
            e.score = self.reranker.score(q_concepts, lang, e)
        evs.sort(key=lambda e: (-e.score, -e.rrf))
        evs = evs[:top_k]
        for i, e in enumerate(evs, 1):
            e.rank = i
        return evs

    # ---------------- admin views ----------------
    def list_documents(self, with_text: bool = False) -> List[dict]:
        return [d.to_dict(with_text) for d in sorted(self.docs.values(), key=lambda d: (d.topic, d.language, -d.version))]

    def stats(self) -> dict:
        ds = list(self.docs.values())
        return {"documents": len(ds), "approved": sum(d.approved for d in ds), "active": sum(d.active for d in ds),
                "retrievable": sum(d.approved and d.active for d in ds), "superseded": sum(not d.active for d in ds),
                "unapproved": sum(not d.approved for d in ds), "review_due": sum(d.review_due() for d in ds if d.active),
                "chunks": len(self.chunks), "embedder": self.embedder.name, "reranker": self.reranker.name,
                "languages": sorted({d.language for d in ds})}
