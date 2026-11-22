"""Shared test helpers: path setup + isolated assistant (no persisted state, temp trace log)."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("AGRI_QUIET", "1")

from assistant_pipeline import AgriAssistant  # noqa: E402
from knowledge_base import KnowledgeBase  # noqa: E402
from observability import Observer  # noqa: E402


def make_assistant(**kw) -> AgriAssistant:
    tmp = Path(tempfile.mkdtemp()) / "traces.jsonl"
    return AgriAssistant(kb=KnowledgeBase(persist=False), observer=Observer(trace_file=tmp), **kw)


def ask(a: AgriAssistant, text: str, **kw) -> dict:
    kw.setdefault("want_tts", False)
    return a.handle_text(text, **kw)
