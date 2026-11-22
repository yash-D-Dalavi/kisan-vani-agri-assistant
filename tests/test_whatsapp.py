import hashlib
import hmac
import json
import unittest

import helpers
from helpers import make_assistant
import audio_service as A
import whatsapp_channel as W


def gateway(consent=False, post=None):
    W.REQUIRE_CONSENT = consent
    return W.WhatsAppGateway(make_assistant(), post=post, sleep=lambda s: None)


class WhatsAppTests(unittest.TestCase):
    def tearDown(self):
        W.REQUIRE_CONSENT = True

    def test_webhook_verification_and_signature(self):
        self.assertEqual(W.verify_challenge({"hub.mode": "subscribe", "hub.verify_token": "agri-demo-verify", "hub.challenge": "42"}), (200, "42"))
        self.assertEqual(W.verify_challenge({"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"})[0], 403)
        body = b'{"a": 1}'
        good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(W.verify_signature("secret", body, good))
        self.assertFalse(W.verify_signature("secret", body, "sha256=deadbeef"))
        self.assertFalse(W.verify_signature("secret", body, ""))
        self.assertTrue(W.verify_signature("", body, ""))          # no secret configured -> not enforced

    def test_text_message_gets_text_and_voice_reply(self):
        gw = gateway()
        out = gw.simulate("919000000001", text="माझ्या कापसाची पाने पिवळी पडत आहेत, काय करावे?")
        kinds = [m["type"] for m in out["replies"]]
        self.assertEqual(kinds, ["text", "audio"])
        self.assertIn("📚", out["replies"][0]["body"])               # citations in the text copy
        self.assertTrue(out["replies"][1]["url"].startswith("/audio/"))

    def test_voice_note_ogg_roundtrip(self):
        gw = gateway()
        raw = A.make_demo_voice_note("गहू पेरणी कधी करावी", "mr", 0.93, 0.02, "ogg")
        out = gw.simulate("919000000002", audio=raw)
        self.assertEqual(out["processed"], 1)
        self.assertIn("गव्हाची", out["replies"][0]["body"])
        self.assertEqual(gw.assistant.observer.snapshot()["channels"], {"whatsapp": 1})

    def test_duplicate_webhook_delivery_ignored(self):
        gw = gateway()
        out = gw.simulate("919000000003", text="गेहूं की बुवाई कब करें?")
        again = gw.process_webhook(out["payload"])                   # Meta retries the same payload
        self.assertEqual((again["processed"], again["duplicates"]), (0, 1))
        self.assertEqual(len([m for m in gw.outbox if m["type"] == "text"]), 1)

    def test_consent_flow_and_data_deletion(self):
        gw = gateway(consent=True)
        r1 = gw.simulate("919000000004", text="कपास में गुलाबी सुंडी")
        self.assertIn("हाँ", r1["replies"][0]["body"])                 # consent prompt, query NOT processed
        self.assertEqual(gw.assistant.observer.snapshot()["requests"], 0)
        gw.simulate("919000000004", text="हाँ")
        r3 = gw.simulate("919000000004", text="कपास में गुलाबी सुंडी")
        self.assertEqual(r3["replies"][0]["type"], "text")
        self.assertEqual(gw.assistant.observer.snapshot()["requests"], 1)
        gw.simulate("919000000004", text="डेटा हटाओ")
        self.assertFalse(gw.assistant.sessions.get("919000000004").get("consent"))

    def test_language_command_persists(self):
        gw = gateway()
        gw.simulate("919000000005", text="भाषा मराठी")
        self.assertEqual(gw.assistant.sessions.get("919000000005")["lang"], "mr")

    def test_unsupported_and_missing_media(self):
        gw = gateway()
        payload = {"entry": [{"changes": [{"value": {"messages": [{"from": "9", "id": "w1", "type": "image", "timestamp": "1"}]}}]}]}
        gw.process_webhook(payload)
        self.assertIn("टेक्स्ट", gw.outbox[-1]["body"])
        payload = {"entry": [{"changes": [{"value": {"messages": [{"from": "9", "id": "w2", "type": "audio", "audio": {"id": "nope", "mime_type": "audio/ogg"}, "timestamp": "1"}]}}]}]}
        gw.process_webhook(payload)
        self.assertIn("डाउनलोड", gw.outbox[-1]["body"])

    def test_status_updates_counted(self):
        gw = gateway()
        gw.process_webhook({"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}, {"status": "read"}, {"status": "failed"}]}}]}]})
        self.assertEqual(gw.status_counts, {"delivered": 1, "read": 1, "failed": 1})

    def test_send_retries_with_backoff_then_gives_up(self):
        calls = []

        def flaky(path, body):
            calls.append(path)
            raise ConnectionError("down")
        gw = gateway(post=flaky)
        self.assertFalse(gw._send("messages", {}))
        self.assertEqual(len(calls), 3)
        self.assertEqual((gw.counters["send_retries"], gw.counters["send_failures"]), (3, 1))


if __name__ == "__main__":
    unittest.main()
