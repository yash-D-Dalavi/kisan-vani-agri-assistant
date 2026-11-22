"""Runs against the REAL FastAPI app when fastapi + httpx are installed (pip install -r requirements.txt); skipped otherwise."""
import unittest

import helpers
from helpers import make_assistant

try:
    from fastapi.testclient import TestClient
    import app as app_module
    HAVE = True
except Exception:  # noqa: BLE001
    HAVE = False

import handlers as H
import whatsapp_channel as W


@unittest.skipUnless(HAVE, "fastapi/httpx not installed")
class FastApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        W.REQUIRE_CONSENT = False
        H.set_services(H.Services(make_assistant()))
        cls.c = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls):
        W.REQUIRE_CONSENT = True
        H.set_services(None)

    def test_core_endpoints(self):
        self.assertEqual(self.c.get("/health").json()["status"], "ok")
        self.assertIn("html", self.c.get("/ui").headers["content-type"])
        r = self.c.post("/query/text", json={"text": "गहू पेरणी कधी करावी?", "tts": False}).json()
        self.assertEqual(r["decision"]["action"], "answer")
        self.assertEqual(self.c.get("/admin/documents").status_code, 200)
        self.assertEqual(self.c.post("/admin/approve", json={"doc_id": "ghost", "approved": True}).status_code, 404)
        self.assertEqual(self.c.get("/metrics").status_code, 200)
        self.assertEqual(self.c.get("/docs").status_code, 200)             # OpenAPI docs
        self.assertIn("/query/voice", self.c.get("/openapi.json").json()["paths"])

    def test_voice_upload(self):
        import audio_service as A
        raw = A.make_demo_voice_note("गहू पेरणी कधी करावी", "mr", 0.93, 0.02, "ogg")
        r = self.c.post("/query/voice", files={"audio": ("n.ogg", raw, "audio/ogg")}, data={"tts": "0"}).json()
        self.assertEqual(r["decision"]["action"], "answer")


if __name__ == "__main__":
    unittest.main()
