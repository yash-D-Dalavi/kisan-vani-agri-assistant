"""
observability.py - Module 13 (logging, latency / cost / fallback tracking, feedback) and the privacy side of Module 12.

  * structured JSONL trace log (logs/traces.jsonl); phone numbers are masked in text and user ids are hashed
  * in-memory metrics: request / decision / reason / intent / language counters, latency p50/p95, fallback rate,
    estimated cost per query, user feedback (thumbs up/down)
  * review queue (Module 14): low-confidence or refused conversations for expert review, with one-click promotion into
    the evaluation set (data/eval_additions.jsonl)
  * Prometheus text exposition (GET /metrics?format=prom) so Grafana / Langfuse-style tooling can scrape it
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import Counter, deque
from typing import Deque, Dict, List, Optional

import config
from eval_metrics import percentile

_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s\-]{8,}\d)(?!\d)")
TRACE_FILE = config.LOG_DIR / "traces.jsonl"
EVAL_ADDITIONS = config.DATA_DIR / "eval_additions.jsonl"

# Illustrative unit costs (INR) for the projected hosted-model stack. Replace with your real vendor pricing.
COST_INR = {"asr_per_min": 0.50, "llm_per_query": 0.15, "tts_per_100_chars": 0.08, "whatsapp_per_message": 0.35}


def mask_pii(text: str) -> str:
    return _PHONE_RE.sub("[phone]", text or "")


def hash_user(user_id: Optional[str]) -> str:
    return hashlib.sha256((user_id or "anon").encode()).hexdigest()[:12]


def estimate_cost_inr(duration_s: float, answer_chars: int, channel: str) -> float:
    c = COST_INR["asr_per_min"] * duration_s / 60 + COST_INR["llm_per_query"] + COST_INR["tts_per_100_chars"] * answer_chars / 100
    if channel == "whatsapp":
        c += 2 * COST_INR["whatsapp_per_message"]
    return round(c, 3)


class Observer:
    def __init__(self, trace_file=TRACE_FILE, keep: int = 500):
        self._lock = threading.Lock()
        self.trace_file = trace_file
        self.started = time.time()
        self.counters: Counter = Counter()
        self.decisions: Counter = Counter()
        self.reasons: Counter = Counter()
        self.intents: Counter = Counter()
        self.langs: Counter = Counter()
        self.channels: Counter = Counter()
        self.latencies: List[float] = []
        self.costs: List[float] = []
        self.feedback: Counter = Counter()
        self.recent: Deque[dict] = deque(maxlen=keep)
        self.review: Deque[dict] = deque(maxlen=keep)

    def record(self, resp: dict) -> None:
        """Called once per handled request with the final response dict."""
        d, lat = resp["decision"], resp["latency"]
        rec = {"ts": resp["timestamp"], "request_id": resp["request_id"], "user": resp.get("user_hash", ""),
               "channel": resp["channel"], "input_type": resp["input"]["type"], "query": mask_pii(resp["input"]["transcript"]),
               "asr_conf": (resp["input"].get("asr") or {}).get("confidence"), "language": resp["language"]["detected"],
               "intent": resp["understanding"]["intent"], "action": d["action"], "reasons": d["reason_codes"],
               "confidence": d["confidence"], "signals": d["signals"], "latency_ms": lat["measured_ms"],
               "projected_s": lat["projected_prod_s"], "cost_inr": resp.get("cost_inr", 0),
               "sources": [c.get("doc_id") or c.get("tool") for c in resp["answer"].get("citations", [])], "tool": (resp.get("tool") or {}).get("tool")}
        with self._lock:
            self.counters["requests"] += 1
            self.decisions[d["action"]] += 1
            for r in d["reason_codes"]:
                self.reasons[r] += 1
            self.intents[rec["intent"]] += 1
            self.langs[rec["language"]] += 1
            self.channels[rec["channel"]] += 1
            self.latencies.append(lat["measured_ms"])
            self.costs.append(rec["cost_inr"])
            self.recent.append(rec)
            if d["action"] != "answer" or d["confidence"] < 0.7:
                self.review.append({**rec, "reviewed": False})
            try:
                with open(self.trace_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def add_feedback(self, request_id: str, rating: str, comment: str = "") -> bool:
        if rating not in ("up", "down"):
            return False
        with self._lock:
            self.feedback[rating] += 1
            if rating == "down":
                for r in self.review:
                    if r["request_id"] == request_id:
                        r["feedback"] = "down"
                        r["comment"] = mask_pii(comment)
                        return True
                for r in self.recent:
                    if r["request_id"] == request_id:
                        self.review.append({**r, "reviewed": False, "feedback": "down", "comment": mask_pii(comment)})
                        break
        return True

    def review_queue(self, limit: int = 50) -> List[dict]:
        with self._lock:
            return list(self.review)[-limit:][::-1]

    def promote_to_eval(self, request_id: str, expected_action: str, note: str = "") -> Optional[dict]:
        """Module 14: add a reviewed conversation to the evaluation set as a regression case."""
        with self._lock:
            rec = next((r for r in self.review if r["request_id"] == request_id), None) or \
                  next((r for r in self.recent if r["request_id"] == request_id), None)
            if not rec:
                return None
            item = {"id": f"review-{request_id[:8]}", "category": "reviewed", "lang": rec["language"], "query": rec["query"],
                    "expect": {"decision": expected_action}, "note": note}
            with open(EVAL_ADDITIONS, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            rec["reviewed"] = True
            return item

    def snapshot(self) -> dict:
        with self._lock:
            n = self.counters["requests"]
            lat = list(self.latencies)
            answered = self.decisions["answer"]
            fb = self.feedback
            return {
                "uptime_s": round(time.time() - self.started, 1), "requests": n, "decisions": dict(self.decisions),
                "fallback_rate": round((n - answered) / n, 3) if n else 0.0,
                "reason_codes": dict(self.reasons.most_common()), "intents": dict(self.intents), "languages": dict(self.langs),
                "channels": dict(self.channels),
                "latency_ms": {"p50": round(percentile(lat, 50), 1), "p95": round(percentile(lat, 95), 1), "max": round(max(lat), 1) if lat else 0.0},
                "avg_cost_inr": round(sum(self.costs) / len(self.costs), 3) if self.costs else 0.0,
                "feedback": {"up": fb["up"], "down": fb["down"]}, "review_pending": sum(1 for r in self.review if not r.get("reviewed")),
                "recent": list(self.recent)[-15:][::-1],
            }

    def prometheus(self) -> str:
        s = self.snapshot()
        lines = ["# TYPE agri_requests_total counter", f"agri_requests_total {s['requests']}"]
        for k, v in s["decisions"].items():
            lines.append(f'agri_decisions_total{{action="{k}"}} {v}')
        for k, v in s["reason_codes"].items():
            lines.append(f'agri_reason_total{{code="{k}"}} {v}')
        lines += [f'agri_latency_ms{{quantile="0.5"}} {s["latency_ms"]["p50"]}', f'agri_latency_ms{{quantile="0.95"}} {s["latency_ms"]["p95"]}',
                  f"agri_fallback_rate {s['fallback_rate']}", f"agri_avg_cost_inr {s['avg_cost_inr']}",
                  f'agri_feedback_total{{rating="up"}} {s["feedback"]["up"]}', f'agri_feedback_total{{rating="down"}} {s["feedback"]["down"]}']
        return "\n".join(lines) + "\n"
