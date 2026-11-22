import array
import shutil
import unittest

import helpers  # noqa: F401
import audio_service as A


class AudioTests(unittest.TestCase):
    def test_sim_tag_roundtrip_wav(self):
        raw = A.make_demo_voice_note("कपास में कीड़े", "hi", 0.9, 0.02, "wav")
        tag = A.decode_sim_tag(raw)
        self.assertEqual(tag["transcript"], "कपास में कीड़े")
        self.assertAlmostEqual(tag["confidence"], 0.9)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_ogg_opus_to_16k_mono_wav(self):
        raw = A.make_demo_voice_note("माझ्या कापसाची पाने पिवळी पडत आहेत", "mr", 0.92, 0.02, "ogg")
        self.assertEqual(raw[:4], b"OggS")
        info = A.normalize_audio(raw, "audio/ogg; codecs=opus")
        self.assertEqual((info.sample_rate, info.channels, info.method), (16000, 1, "ffmpeg"))
        self.assertGreater(info.duration_s, 1.0)
        self.assertGreater(info.speech_ratio, 0.3)
        res = A.transcribe(raw, info, "mr")
        self.assertEqual(res.backend, "simulated")
        self.assertEqual(res.text, "माझ्या कापसाची पाने पिवळी पडत आहेत")

    def test_wav_fallback_without_ffmpeg(self):
        raw = A.make_demo_voice_note("गहू पेरणी कधी करावी", "mr", 0.9, 0.02, "wav")
        orig = A.shutil.which
        A.shutil.which = lambda name: None          # simulate a machine without ffmpeg
        try:
            info = A.normalize_audio(raw, "audio/wav")
        finally:
            A.shutil.which = orig
        self.assertEqual(info.method, "python-wave")
        self.assertIn("decoded_without_ffmpeg", info.warnings)

    def test_silent_audio_flagged(self):
        silent = A._wav_bytes(array.array("h", [0]) * 16000)
        info = A.normalize_audio(silent, "audio/wav")
        self.assertIn("near_silent", info.warnings)
        self.assertLess(info.quality, 0.5)

    def test_garbage_raises_decode_error(self):
        orig = A.shutil.which
        A.shutil.which = lambda name: None
        try:
            with self.assertRaises(A.AudioDecodeError):
                A.normalize_audio(b"not audio at all" * 10, "audio/ogg")
        finally:
            A.shutil.which = orig

    def test_real_speech_without_backend_returns_empty_not_fake(self):
        pcm = A._speechlike(2.0, 0.02, 1)
        raw = A._wav_bytes(pcm)                      # no transcript tag = "real" audio for the simulated backend
        info = A.normalize_audio(raw, "audio/wav")
        res = A.transcribe(raw, info, "hi")
        self.assertEqual((res.text, res.confidence), ("", 0.0))
        hinted = A.transcribe(raw, info, "hi", asr_hint="कपास", asr_hint_conf=0.8)
        self.assertEqual((hinted.backend, hinted.text), ("browser-webspeech", "कपास"))

    def test_registered_backend_and_crash_isolation(self):
        A.register_asr("boom", lambda raw, info, lang: 1 / 0)
        raw = A.make_demo_voice_note("x y z", "hi", 0.9, 0.0, "wav")
        info = A.normalize_audio(raw, "audio/wav")
        res = A.transcribe(raw, info, "hi", backend="boom")
        self.assertEqual(res.confidence, 0.0)
        self.assertIn("backend error", res.note)

    def test_tts_placeholder_duration_and_compression(self):
        txt = "यह एक परीक्षण वाक्य है। " * 5
        res = A.synthesize(txt, "hi", backend="simulated")
        self.assertTrue(res.synthetic_placeholder)
        self.assertAlmostEqual(res.duration_s, A.estimate_speech_seconds(txt), delta=0.2)
        if shutil.which("ffmpeg"):
            self.assertGreater(res.opus_bytes, 0)
            self.assertLess(res.opus_bytes, res.bytes / 4)   # OGG/Opus voice note is far smaller than WAV

    def test_purge_old_audio(self):
        A.synthesize("परीक्षण", "hi", backend="simulated")
        self.assertGreaterEqual(A.purge_old_audio(0), 1)


if __name__ == "__main__":
    unittest.main()
