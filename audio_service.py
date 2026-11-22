"""
audio_service.py - Module 1 (audio interface), Module 2 (ASR) and Module 11 (TTS) of the blueprint.

Design: every heavy dependency is optional and every step has a fallback, so the whole pipeline runs on a laptop with
only the Python standard library (+ ffmpeg for OGG/Opus, which is optional too).

  normalize_audio()  OGG/Opus | MP3 | M4A | WebM | WAV  ->  16 kHz mono 16-bit WAV   (ffmpeg -> stdlib wave -> librosa)
                     + quality metrics: RMS, peak, clipping, energy VAD (speech ratio), rough SNR
  transcribe()       pluggable ASR registry: simulated | browser-webspeech | whisper | bhashini  (+ register_asr())
  synthesize()       pluggable TTS: gtts | edge-tts | simulated placeholder  (+ OGG/Opus voice-note conversion)
  make_demo_voice_note()  builds demo voice notes (speech-like audio + embedded transcript tag) so the *entire* audio
                     path (OGG -> WAV -> ASR -> pipeline) can be demonstrated without a microphone or a model.

Honesty: the "simulated" ASR cannot understand real human speech. It reads the transcript tag embedded in the demo
audio generated here.  For real speech use the whisper / bhashini backends or the browser Web-Speech hint.
"""
from __future__ import annotations

import array
import base64
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

import config

TARGET_SR = 16000
FRAME = 320  # 20 ms at 16 kHz


class AudioDecodeError(Exception):
    """Raised when no available decoder can read the input audio."""


# ----------------------------------------------------------------------------------------------------------------------
# Module 1: normalisation + quality metrics
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class AudioInfo:
    path: str
    duration_s: float
    sample_rate: int = TARGET_SR
    channels: int = 1
    method: str = ""
    input_mime: str = ""
    input_bytes: int = 0
    output_bytes: int = 0
    rms_dbfs: float = -99.0
    peak: float = 0.0
    clipping_ratio: float = 0.0
    speech_ratio: float = 0.0
    speech_s: float = 0.0
    snr_db: float = 0.0
    gain_applied_db: float = 0.0
    quality: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = os.path.basename(self.path)
        return d


def sniff_extension(data: bytes, mime: str = "", filename: str = "") -> str:
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data[:4] == b"\x1aE\xdf\xa3":
        return ".webm"
    if data[4:8] == b"ftyp":
        return ".m4a"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    ext = Path(filename).suffix.lower()
    if ext:
        return ext
    if "ogg" in mime or "opus" in mime:
        return ".ogg"
    if "webm" in mime:
        return ".webm"
    return ".bin"


def _read_wav_samples(path: str):
    with wave.open(path, "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sw == 2:
        s = array.array("h")
        s.frombytes(raw)
        if struct.pack("=H", 1) != struct.pack("<H", 1):
            s.byteswap()
    elif sw == 1:  # unsigned 8-bit
        s = array.array("h", [(b - 128) << 8 for b in raw])
    else:
        raise AudioDecodeError(f"unsupported WAV sample width {sw}")
    if ch > 1:
        s = array.array("h", [int(sum(s[i:i + ch]) / ch) for i in range(0, len(s) - ch + 1, ch)])
    return s, sr


def _resample_linear(s: array.array, sr_in: int, sr_out: int = TARGET_SR) -> array.array:
    if sr_in == sr_out or not len(s):
        return s
    ratio = sr_in / sr_out
    n_out = int(len(s) / ratio)
    out = array.array("h", [0]) * n_out
    for i in range(n_out):
        pos = i * ratio
        j = int(pos)
        frac = pos - j
        a = s[j]
        b = s[j + 1] if j + 1 < len(s) else a
        out[i] = int(a + (b - a) * frac)
    return out


def _wav_bytes(samples: array.array, sr: int = TARGET_SR, comment: str = "") -> bytes:
    """Build a mono 16-bit PCM WAV (optionally with a LIST/INFO ICMT comment chunk)."""
    data = samples.tobytes() if struct.pack("=H", 1) == struct.pack("<H", 1) else _swap(samples)
    fmt = struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    if len(data) % 2:
        body += b"\x00"
    if comment:
        c = comment.encode("utf-8") + b"\x00"
        if len(c) % 2:
            c += b"\x00"
        info = b"INFO" + b"ICMT" + struct.pack("<I", len(c)) + c
        body += b"LIST" + struct.pack("<I", len(info)) + info
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _swap(samples: array.array) -> bytes:
    c = array.array("h", samples)
    c.byteswap()
    return c.tobytes()


def _analyse(s: array.array) -> dict:
    """Energy-based VAD + level/clipping/SNR metrics on 20 ms frames (pure python, no numpy needed)."""
    n = len(s)
    if n == 0:
        return dict(rms_dbfs=-99.0, peak=0.0, clip=0.0, speech_ratio=0.0, snr=0.0, frames=[])
    peak = max(max(s), -min(s)) / 32768.0
    clip = sum(1 for v in s if v >= 32000 or v <= -32000) / n
    energies = []
    for i in range(0, n - FRAME + 1, FRAME):
        fr = s[i:i + FRAME]
        energies.append(sum(v * v for v in fr) / FRAME)
    if not energies:
        energies = [sum(v * v for v in s) / n]
    mean_e = sum(energies) / len(energies)
    rms_dbfs = 10 * math.log10(max(mean_e, 1e-9) / (32768.0 ** 2))
    ordered = sorted(energies)
    floor = ordered[max(0, int(0.1 * len(ordered)) - 1)] or 1.0
    thr = max(floor * 4.0, (32768.0 ** 2) * 10 ** (-48 / 10))  # >= -48 dBFS and 6 dB above the noise floor
    speech_frames = sum(1 for e in energies if e > thr)
    top = ordered[int(0.7 * len(ordered)):] or ordered
    bot = ordered[: max(1, int(0.2 * len(ordered)))]
    snr = 10 * math.log10(max(sum(top) / len(top), 1e-9) / max(sum(bot) / len(bot), 1.0))
    return dict(rms_dbfs=rms_dbfs, peak=peak, clip=clip, speech_ratio=speech_frames / len(energies),
                snr=max(0.0, min(40.0, snr)), frames=energies)


def _finish(path: str, method: str, mime: str, in_bytes: int, warnings: List[str]) -> AudioInfo:
    s, sr = _read_wav_samples(path)
    if sr != TARGET_SR:
        s = _resample_linear(s, sr)
    gain_db = 0.0
    a = _analyse(s)
    # gentle peak normalisation for quiet voice notes (max +12 dB) - helps ASR on whispered / distant recordings
    if 0 < a["peak"] < 0.30:
        gain = min(0.7 / a["peak"], 4.0)
        s = array.array("h", [max(-32768, min(32767, int(v * gain))) for v in s])
        gain_db = 20 * math.log10(gain)
        a = _analyse(s)
    with open(path, "wb") as f:
        f.write(_wav_bytes(s, TARGET_SR))
    dur = len(s) / TARGET_SR
    speech_s = a["speech_ratio"] * dur
    if dur < 0.6:
        warnings.append("very_short_audio")
    if a["rms_dbfs"] < -55 or a["speech_ratio"] < 0.05:
        warnings.append("near_silent")
    if a["clip"] > 0.01:
        warnings.append("clipping")
    if a["snr"] < 8 and "near_silent" not in warnings:
        warnings.append("low_snr_noisy")
    q = (0.4 * min(1.0, a["snr"] / 20) + 0.3 * min(1.0, speech_s / 1.5) + 0.2 * (1 - min(1.0, a["clip"] * 20))
         + 0.1 * (1.0 if a["rms_dbfs"] > -50 else 0.0))
    return AudioInfo(path=path, duration_s=round(dur, 2), method=method, input_mime=mime, input_bytes=in_bytes,
                     output_bytes=os.path.getsize(path), rms_dbfs=round(a["rms_dbfs"], 1), peak=round(a["peak"], 3),
                     clipping_ratio=round(a["clip"], 4), speech_ratio=round(a["speech_ratio"], 3),
                     speech_s=round(speech_s, 2), snr_db=round(a["snr"], 1), gain_applied_db=round(gain_db, 1),
                     quality=round(max(0.0, min(1.0, q)), 3), warnings=warnings)


def normalize_audio(data: bytes, mime: str = "", filename: str = "") -> AudioInfo:
    """Decode any supported voice note to 16 kHz mono WAV in AUDIO_DIR and compute quality metrics."""
    if not data:
        raise AudioDecodeError("empty audio payload")
    ext = sniff_extension(data, mime, filename)
    uid = uuid.uuid4().hex[:12]
    src = config.AUDIO_DIR / f"src_{uid}{ext}"
    dst = config.AUDIO_DIR / f"in_{uid}.wav"
    src.write_bytes(data)
    warnings: List[str] = []
    try:
        # 1) ffmpeg: the production path for WhatsApp OGG/Opus
        if shutil.which("ffmpeg"):
            cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(TARGET_SR),
                   "-af", "highpass=f=60", "-sample_fmt", "s16", str(dst)]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=40)
                if r.returncode == 0 and dst.exists() and dst.stat().st_size > 44:
                    return _finish(str(dst), "ffmpeg", mime, len(data), warnings)
                warnings.append("ffmpeg_failed:" + r.stderr.decode("utf-8", "ignore")[:120].strip())
            except (subprocess.TimeoutExpired, OSError) as e:
                warnings.append(f"ffmpeg_error:{type(e).__name__}")
        # 2) stdlib wave: handles plain PCM WAV (resamples/downmixes by itself)
        if ext == ".wav":
            try:
                s, sr = _read_wav_samples(str(src))
                s = _resample_linear(s, sr)
                dst.write_bytes(_wav_bytes(s, TARGET_SR))
                warnings.append("decoded_without_ffmpeg")
                return _finish(str(dst), "python-wave", mime, len(data), warnings)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"wave_failed:{e}")
        # 3) librosa (optional)
        try:
            import librosa  # type: ignore
            import numpy as np  # type: ignore
            y, _ = librosa.load(str(src), sr=TARGET_SR, mono=True)
            pcm = array.array("h", (np.clip(y, -1, 1) * 32767).astype("int16").tolist())
            dst.write_bytes(_wav_bytes(pcm, TARGET_SR))
            return _finish(str(dst), "librosa", mime, len(data), warnings)
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            warnings.append(f"librosa_failed:{e}")
        raise AudioDecodeError("could not decode audio (install ffmpeg for OGG/Opus/MP3/WebM): " + "; ".join(warnings))
    finally:
        try:
            src.unlink()
        except OSError:
            pass


def purge_old_audio(max_age_s: int = 3600) -> int:
    """Data-minimisation (Module 12): delete stored audio older than max_age_s. Returns the number of files removed."""
    now, removed = time.time(), 0
    for p in config.AUDIO_DIR.glob("*"):
        try:
            if p.is_file() and now - p.stat().st_mtime > max_age_s:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


# ----------------------------------------------------------------------------------------------------------------------
# Demo voice notes (speech-like audio + embedded transcript tag)
# ----------------------------------------------------------------------------------------------------------------------
SIM_MARKER = "AGRISIM1:"
_SIM_RE = re.compile(rb"AGRISIM1:([A-Za-z0-9_\-=]+)~")   # "~" terminator: Ogg pages follow the tag directly


def encode_sim_tag(transcript: str, lang: str, conf: float) -> str:
    payload = json.dumps({"t": transcript, "l": lang, "c": conf}, ensure_ascii=False).encode("utf-8")
    return SIM_MARKER + base64.urlsafe_b64encode(payload).decode("ascii") + "~"


def decode_sim_tag(raw: bytes) -> Optional[dict]:
    m = _SIM_RE.search(raw)
    if not m:
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(m.group(1)).decode("utf-8"))
        return {"transcript": d["t"], "language": d.get("l"), "confidence": float(d.get("c", 0.9))}
    except Exception:  # noqa: BLE001
        return None


def _speechlike(duration_s: float, noise: float, seed: int) -> array.array:
    """Syllable-modulated harmonic signal (not real speech - a stand-in so the audio path has real samples)."""
    import random
    rnd = random.Random(seed)
    n = int(duration_s * TARGET_SR)
    out = array.array("h", [0]) * n
    f0 = rnd.uniform(110, 170)
    syl_rate = rnd.uniform(3.5, 5.0)
    for i in range(n):
        t = i / TARGET_SR
        env = max(0.0, math.sin(math.pi * ((t * syl_rate) % 1.0))) ** 1.5
        if (t * syl_rate) % 6.0 > 5.4:  # a short pause now and then
            env *= 0.1
        f = f0 * (1 + 0.15 * math.sin(2 * math.pi * 0.7 * t))
        v = (math.sin(2 * math.pi * f * t) + 0.5 * math.sin(2 * math.pi * 2 * f * t) + 0.25 * math.sin(2 * math.pi * 3 * f * t)) / 1.75
        v = v * env * 0.55 + noise * rnd.uniform(-1, 1) * 0.5
        out[i] = int(max(-1.0, min(1.0, v)) * 30000)
    return out


def make_demo_voice_note(transcript: str, lang: str = "hi", asr_conf: float = 0.92, noise: float = 0.02,
                         fmt: str = "wav", seed: Optional[int] = None) -> bytes:
    """Return a demo voice note (wav | ogg) carrying the transcript tag for the simulated ASR."""
    words = max(3, len(transcript.split()))
    dur = max(1.8, min(8.0, words * 0.42))
    pcm = _speechlike(dur, noise, seed if seed is not None else sum(map(ord, transcript)) % 9973)
    tag = encode_sim_tag(transcript, lang, asr_conf)
    wav = _wav_bytes(pcm, TARGET_SR, comment=tag)
    if fmt == "wav":
        return wav
    if fmt in ("ogg", "opus"):
        if not shutil.which("ffmpeg"):
            return wav
        with tempfile.TemporaryDirectory() as td:
            i, o = os.path.join(td, "in.wav"), os.path.join(td, "out.ogg")
            Path(i).write_bytes(wav)
            r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", i, "-c:a", "libopus", "-b:a", "24k",
                                "-application", "voip", "-metadata", f"comment={tag}", o], capture_output=True, timeout=40)
            if r.returncode == 0 and os.path.exists(o):
                return Path(o).read_bytes()
        return wav
    raise ValueError("fmt must be wav or ogg")


# ----------------------------------------------------------------------------------------------------------------------
# Module 2: ASR registry
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class ASRResult:
    text: str
    language: Optional[str]
    confidence: float
    backend: str
    note: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


ASRFunc = Callable[[bytes, AudioInfo, Optional[str]], ASRResult]
_ASR: Dict[str, ASRFunc] = {}


def register_asr(name: str, fn: ASRFunc) -> None:
    """Plug in any ASR: fn(raw_bytes, audio_info, lang_hint) -> ASRResult."""
    _ASR[name] = fn


def _asr_simulated(raw: bytes, info: AudioInfo, lang_hint: Optional[str]) -> ASRResult:
    tag = decode_sim_tag(raw)
    if tag:
        conf = tag["confidence"]
        if info.speech_s < 0.4:
            conf *= 0.5
        return ASRResult(tag["transcript"], tag["language"] or lang_hint, round(conf, 3), "simulated",
                         "transcript read from the demo voice-note tag (simulated ASR)")
    return ASRResult("", lang_hint, 0.0, "simulated",
                     "simulated ASR cannot transcribe real speech - set AGRI_ASR_BACKEND=whisper, or use the browser Web-Speech hint")


_WHISPER = {}


def _asr_whisper(raw: bytes, info: AudioInfo, lang_hint: Optional[str]) -> ASRResult:
    """faster-whisper backend (pip install faster-whisper). Confidence = mean segment probability * (1 - no_speech)."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return ASRResult("", lang_hint, 0.0, "whisper", "faster-whisper not installed (pip install faster-whisper)")
    size = os.getenv("AGRI_WHISPER_MODEL", "small")
    if size not in _WHISPER:
        _WHISPER[size] = WhisperModel(size, device="cpu", compute_type="int8")
    # initial prompt with domain vocabulary biases decoding toward crop / scheme names (helps code-mixed speech)
    prompt = "कपास सोयाबीन गेहूं प्याज टमाटर मंडी भाव पीएम किसान फसल बीमा कापूस कांदा बाजारभाव हवामान"
    segs, meta = _WHISPER[size].transcribe(info.path, language=lang_hint, beam_size=5, vad_filter=True,
                                           initial_prompt=prompt)
    segs = list(segs)
    if not segs:
        return ASRResult("", getattr(meta, "language", lang_hint), 0.0, "whisper", "no speech detected")
    conf = sum(math.exp(s.avg_logprob) * (1 - s.no_speech_prob) for s in segs) / len(segs)
    return ASRResult(" ".join(s.text.strip() for s in segs), getattr(meta, "language", lang_hint), round(conf, 3), "whisper")


def _asr_bhashini(raw: bytes, info: AudioInfo, lang_hint: Optional[str]) -> ASRResult:
    """Bhashini / Dhruva pipeline ASR. Needs BHASHINI_API_KEY and BHASHINI_ASR_SERVICE_ID in the environment.
    NOTE: written to the public Dhruva pipeline format but not exercised in the offline sandbox - verify the service id
    and response shape in your Bhashini account before relying on it."""
    key, service = os.getenv("BHASHINI_API_KEY"), os.getenv("BHASHINI_ASR_SERVICE_ID")
    if not key or not service:
        return ASRResult("", lang_hint, 0.0, "bhashini", "BHASHINI_API_KEY / BHASHINI_ASR_SERVICE_ID not set")
    import urllib.request
    body = {"pipelineTasks": [{"taskType": "asr", "config": {"language": {"sourceLanguage": lang_hint or "hi"},
                                                              "serviceId": service, "audioFormat": "wav",
                                                              "samplingRate": TARGET_SR}}],
            "inputData": {"audio": [{"audioContent": base64.b64encode(Path(info.path).read_bytes()).decode()}]}}
    req = urllib.request.Request(os.getenv("BHASHINI_URL", "https://dhruva-api.bhashini.gov.in/services/inference/pipeline"),
                                 data=json.dumps(body).encode(), headers={"Authorization": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read().decode())
        text = out["pipelineResponse"][0]["output"][0]["source"]
        return ASRResult(text, lang_hint, 0.8, "bhashini", "Bhashini returns no confidence; fixed 0.8 prior used")
    except Exception as e:  # noqa: BLE001
        return ASRResult("", lang_hint, 0.0, "bhashini", f"request failed: {type(e).__name__}")


register_asr("simulated", _asr_simulated)
register_asr("whisper", _asr_whisper)
register_asr("bhashini", _asr_bhashini)


def transcribe(raw: bytes, info: AudioInfo, lang_hint: Optional[str] = None, backend: Optional[str] = None,
               asr_hint: Optional[str] = None, asr_hint_conf: Optional[float] = None) -> ASRResult:
    """Run ASR. A browser Web-Speech hint (real recognition done client-side) is used when the server backend is simulated
    and the audio carries no demo tag."""
    t0 = time.perf_counter()
    name = backend or config.ASR_BACKEND
    fn = _ASR.get(name) or _ASR["simulated"]
    try:
        res = fn(raw, info, lang_hint)
    except Exception as e:  # noqa: BLE001 - a crashed ASR must never crash the pipeline
        res = ASRResult("", lang_hint, 0.0, name, f"backend error: {type(e).__name__}: {e}")
    if not res.text and asr_hint:
        res = ASRResult(asr_hint, lang_hint, float(asr_hint_conf if asr_hint_conf is not None else 0.75),
                        "browser-webspeech", "transcript produced in the browser (Web Speech API)")
    res.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return res


# ----------------------------------------------------------------------------------------------------------------------
# Module 11: TTS
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class TTSResult:
    filename: str = ""
    path: str = ""
    format: str = ""
    duration_s: float = 0.0
    backend: str = ""
    synthetic_placeholder: bool = False
    bytes: int = 0
    opus_bytes: int = 0
    error: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d


def estimate_speech_seconds(text: str) -> float:
    """Rough spoken duration (chars incl. spaces / chars-per-second). Used for the <30 s reply budget."""
    return round(len(text.strip()) / config.T.speech_chars_per_second, 1)


def _run_with_timeout(fn: Callable[[], None], timeout: float) -> Optional[str]:
    err: List[str] = []

    def target():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            err.append(f"{type(e).__name__}: {e}")

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return "timeout"
    return err[0] if err else None


def _tts_gtts(text: str, lang: str, out: Path) -> Optional[str]:
    try:
        from gtts import gTTS  # type: ignore
    except ImportError:
        return "gtts not installed"
    return _run_with_timeout(lambda: gTTS(text=text, lang=lang).save(str(out)), 10)


def _tts_edge(text: str, lang: str, out: Path) -> Optional[str]:
    try:
        import asyncio
        import edge_tts  # type: ignore
    except ImportError:
        return "edge-tts not installed"
    voice = {"hi": "hi-IN-SwaraNeural", "mr": "mr-IN-AarohiNeural", "en": "en-IN-NeerjaNeural"}.get(lang, "hi-IN-SwaraNeural")
    return _run_with_timeout(lambda: asyncio.run(edge_tts.Communicate(text, voice).save(str(out))), 12)


def _tts_simulated(text: str, out: Path) -> None:
    """Placeholder audio of the right *length* (two soft alternating tones). The UI additionally speaks the text with the
    browser's speechSynthesis voice so a human-sounding reply is heard in the demo."""
    dur = max(1.0, min(60.0, estimate_speech_seconds(text)))
    n = int(dur * TARGET_SR)
    pcm = array.array("h", [0]) * n
    for i in range(n):
        t = i / TARGET_SR
        env = 0.5 - 0.5 * math.cos(2 * math.pi * min(t, dur - t, 0.15) / 0.3) if min(t, dur - t) < 0.15 else 1.0
        f = 392 if int(t * 2) % 2 == 0 else 330
        pcm[i] = int(0.12 * env * math.sin(2 * math.pi * f * t) * 32767)
    out.write_bytes(_wav_bytes(pcm, TARGET_SR))


def to_voice_note_ogg(src: str) -> Optional[str]:
    """Compress to OGG/Opus 24 kbps mono - the format WhatsApp plays as a voice note and ~10x smaller than WAV."""
    if not shutil.which("ffmpeg"):
        return None
    dst = str(Path(src).with_suffix(".ogg"))
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
                        "-application", "voip", dst], capture_output=True, timeout=40)
    return dst if r.returncode == 0 and os.path.exists(dst) else None


def synthesize(text: str, lang: str = "hi", backend: Optional[str] = None) -> TTSResult:
    """Convert the reply to speech. Order for 'auto': gTTS -> edge-tts -> simulated placeholder."""
    t0 = time.perf_counter()
    name = backend or config.TTS_BACKEND
    uid = uuid.uuid4().hex[:12]
    order = {"auto": ["gtts", "edge", "simulated"], "gtts": ["gtts", "simulated"], "edge": ["edge", "simulated"]}.get(name, ["simulated"])
    errors: List[str] = []
    for b in order:
        if b == "simulated":
            out = config.AUDIO_DIR / f"tts_{uid}.wav"
            _tts_simulated(text, out)
            res = TTSResult(out.name, str(out), "wav", estimate_speech_seconds(text), "simulated", True, out.stat().st_size,
                            error="; ".join(errors))
        else:
            out = config.AUDIO_DIR / f"tts_{uid}.mp3"
            err = _tts_gtts(text, lang, out) if b == "gtts" else _tts_edge(text, lang, out)
            if err or not out.exists() or out.stat().st_size < 200:
                errors.append(f"{b}: {err or 'empty output'}")
                continue
            res = TTSResult(out.name, str(out), "mp3", estimate_speech_seconds(text), b, False, out.stat().st_size)
        ogg = to_voice_note_ogg(res.path)
        if ogg:
            res.opus_bytes = os.path.getsize(ogg)
        res.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return res
    return TTSResult(error="; ".join(errors))  # unreachable (simulated never fails) but keeps the type honest
