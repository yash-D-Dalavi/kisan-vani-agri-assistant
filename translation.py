"""
translation.py - Module 4 (Translation) of the blueprint.

The default design is *direct multilingual retrieval* (glossary concept ids + optional BGE-M3) so translation is only needed
when the best evidence is in a different language from the farmer's.  This module provides:

  * glossary protection: crop / pest / chemical / scheme names are masked before MT and restored after, so they are never
    mistranslated (blueprint 4 + 6.2 "integrate the glossary into translation")
  * a registry of translators: "none" (default, honest pass-through), "nllb" (facebook/nllb-200 via transformers, optional),
    and register_translator() for IndicTrans2 / Bhashini
  * ensure_language(): translate if possible, otherwise keep the source language and say so (never silently wrong)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Tuple

import glossary as G
from seed_data import GLOSSARY_TERMS

NLLB_CODES = {"hi": "hin_Deva", "mr": "mar_Deva", "en": "eng_Latn"}


@dataclass
class TranslationResult:
    text: str
    backend: str
    translated: bool
    src: str
    tgt: str
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def protect_terms(text: str, src: str, tgt: str) -> Tuple[str, Dict[str, str]]:
    """Replace glossary terms (source-language surface form) with placeholders mapped to the TARGET-language term."""
    mapping: Dict[str, str] = {}
    out = text
    n = 0
    for term, tr in sorted(GLOSSARY_TERMS.items(), key=lambda kv: -len(kv[1].get(src, ""))):
        s, t = tr.get(src), tr.get(tgt)
        if s and t and s in out:
            ph = f"XTERM{n}X"
            out = out.replace(s, ph)
            mapping[ph] = t
            n += 1
    return out, mapping


def restore_terms(text: str, mapping: Dict[str, str]) -> str:
    for ph, t in mapping.items():
        text = text.replace(ph, t)
    return text


Translator = Callable[[str, str, str], Optional[str]]
_TRANSLATORS: Dict[str, Translator] = {}


def register_translator(name: str, fn: Translator) -> None:
    """fn(text, src, tgt) -> translated text, or None if it cannot translate."""
    _TRANSLATORS[name] = fn


_NLLB = {}


def _nllb(text: str, src: str, tgt: str) -> Optional[str]:
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore
    except ImportError:
        return None
    if "m" not in _NLLB:
        name = "facebook/nllb-200-distilled-600M"
        _NLLB["t"], _NLLB["m"] = AutoTokenizer.from_pretrained(name), AutoModelForSeq2SeqLM.from_pretrained(name)
    tok, model = _NLLB["t"], _NLLB["m"]
    tok.src_lang = NLLB_CODES[src]
    enc = tok(text, return_tensors="pt")
    out = model.generate(**enc, forced_bos_token_id=tok.convert_tokens_to_ids(NLLB_CODES[tgt]), max_new_tokens=256)
    return tok.batch_decode(out, skip_special_tokens=True)[0]


register_translator("nllb", _nllb)


def translate(text: str, src: str, tgt: str, backend: str = "none") -> TranslationResult:
    if src == tgt:
        return TranslationResult(text, "identity", False, src, tgt)
    fn = _TRANSLATORS.get(backend)
    if fn is None:
        return TranslationResult(text, "none", False, src, tgt, ["no translation backend configured"])
    masked, mapping = protect_terms(text, src, tgt)
    try:
        out = fn(masked, src, tgt)
    except Exception as e:  # noqa: BLE001
        return TranslationResult(text, backend, False, src, tgt, [f"translator error: {type(e).__name__}"])
    if out is None:
        return TranslationResult(text, backend, False, src, tgt, [f"backend '{backend}' unavailable"])
    out = restore_terms(out, mapping)
    warn = [] if all(t in out for t in mapping.values()) else ["glossary term lost in translation"]
    return TranslationResult(out, backend, True, src, tgt, warn)


LANG_NOTE = {
    "hi": "(यह जानकारी {src} भाषा में उपलब्ध स्रोत से है।)",
    "mr": "(ही माहिती {src} भाषेतील स्रोताकडून आहे.)",
    "en": "(This information comes from a source in {src}.)",
}


def ensure_language(text: str, src: str, tgt: str, backend: str = "none") -> Tuple[str, Optional[TranslationResult], Optional[str]]:
    """Return (text_in_best_available_language, translation_result, note). Falls back to source language + a localized note."""
    if src == tgt:
        return text, None, None
    res = translate(text, src, tgt, backend)
    if res.translated:
        return res.text, res, None
    name = G.localized({"hi": {"en": "Hindi", "hi": "हिंदी", "mr": "हिंदी"}, "mr": {"en": "Marathi", "hi": "मराठी", "mr": "मराठी"},
                        "en": {"en": "English", "hi": "अंग्रेज़ी", "mr": "इंग्रजी"}}, src, tgt)
    return text, res, LANG_NOTE.get(tgt, LANG_NOTE["en"]).format(src=name)
