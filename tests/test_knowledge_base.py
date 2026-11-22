import unittest

import helpers  # noqa: F401
from knowledge_base import KnowledgeBase, chunk_text
import config


def new_doc(**kw):
    d = dict(doc_id="t1", topic="maize_sowing", title="मक्का बुवाई", text="मक्का की बुवाई जून में करें। बीज उपचार जरूर करें।", language="hi",
             crop="maize", region="Maharashtra", season="kharif", source="Test source", publisher="Test publisher", doc_date="2026-08-01",
             license="test", version=1)
    d.update(kw)
    return d


class KBTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase(persist=False)

    def test_unapproved_document_is_never_retrievable(self):
        ev = self.kb.search("कपास पर दवा की दोगुनी मात्रा", lang="hi", crop="cotton")
        self.assertTrue(ev)
        self.assertNotIn("rumor_spray_hi", {e.chunk.doc_id for e in ev})

    def test_superseded_version_not_retrievable_and_flagged(self):
        old = self.kb.docs["cotton_pbw_hi_v1"]
        self.assertFalse(old.active)
        self.assertEqual(old.superseded_by, "cotton_pbw_hi")
        ev = self.kb.search("कपास गुलाबी सुंडी फेरोमोन ट्रैप", lang="hi", crop="cotton")
        self.assertNotIn("cotton_pbw_hi_v1", {e.chunk.doc_id for e in ev})

    def test_crop_metadata_filter(self):
        ev = self.kb.search("पत्तियों पर धब्बे रोग", lang="hi", crop="cotton")
        self.assertTrue(all(e.chunk.crop in ("cotton", "all") for e in ev))

    def test_region_filter(self):
        ev = self.kb.search("गेहूं की बुवाई कब करें", lang="hi", crop="wheat", region="Delhi")
        self.assertNotIn("wheat_sowing", {e.chunk.topic for e in ev})                 # Maharashtra-only advice is filtered out
        self.assertTrue(not ev or ev[0].score < config.T.retrieval_min)               # leftovers are irrelevant -> gate refuses
        self.assertTrue(self.kb.search("गेहूं की बुवाई कब करें", lang="hi", crop="wheat", region="Maharashtra"))

    def test_cross_lingual_retrieval(self):
        ev = self.kb.search("कांदा साठवणूक कशी करावी", lang="mr", crop="onion")
        self.assertEqual(ev[0].chunk.topic, "onion_storage")
        self.assertEqual(ev[0].chunk.language, "mr")          # same-language chunk wins the language bonus
        ev_en = self.kb.search("store onions after harvest", lang="en", crop="onion")
        self.assertEqual(ev_en[0].chunk.topic, "onion_storage")

    def test_hybrid_scores_exposed(self):
        e = self.kb.search("पीएम किसान योजना", lang="hi")[0]
        self.assertGreater(e.dense, 0)
        self.assertGreater(e.bm25, 0)
        self.assertGreater(e.rrf, 0)
        self.assertGreater(e.score, 0.5)

    def test_ingest_validation_duplicates_and_approval(self):
        self.assertEqual(self.kb.add_document(new_doc(source="")).status, "rejected")
        self.assertEqual(self.kb.add_document(new_doc(doc_date="01-08-2026")).status, "rejected")
        r = self.kb.add_document(new_doc())
        self.assertEqual(r.status, "added")
        self.assertIn("awaiting approval", r.message)
        self.assertNotIn("t1", {e.chunk.doc_id for e in self.kb.search("मक्का बुवाई", lang="hi", crop="maize")})   # unapproved -> invisible
        self.assertEqual(self.kb.add_document(new_doc(doc_id="t2")).status, "duplicate")     # same text
        self.assertTrue(self.kb.set_approval("t1", True))
        self.assertEqual(self.kb.search("मक्का बुवाई", lang="hi", crop="maize")[0].chunk.doc_id, "t1")
        self.assertTrue(self.kb.set_approval("t1", False))
        self.assertNotIn("t1", {e.chunk.doc_id for e in self.kb.search("मक्का बुवाई", lang="hi", crop="maize")})

    def test_new_version_supersedes_old(self):
        self.kb.add_document(new_doc(), approve=True)
        r = self.kb.add_document(new_doc(doc_id="t1b", version=2, text="मक्का की बुवाई जुलाई के पहले सप्ताह में करें। बीज उपचार करें।"), approve=True)
        self.assertEqual(r.superseded, ["t1"])
        self.assertFalse(self.kb.docs["t1"].active)
        top = self.kb.search("मक्का बुवाई", lang="hi", crop="maize")[0]
        self.assertEqual(top.chunk.doc_id, "t1b")
        older = self.kb.add_document(new_doc(doc_id="t0", version=1, text="मक्का पुरानी सलाह बिल्कुल अलग शब्दों में लिखी हुई।"))
        self.assertEqual(older.status, "archived_older_version")

    def test_chunking_overlap_and_size(self):
        text = " ".join(f"यह वाक्य संख्या {i} है।" for i in range(40))
        chunks = chunk_text(text, max_chars=200, overlap=1)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) < 260 for c in chunks))
        self.assertIn(chunks[0].split("। ")[-1].strip("।"), chunks[1])   # last sentence repeated = overlap

    def test_stats_and_review_due(self):
        st = self.kb.stats()
        self.assertEqual(st["unapproved"], 1)
        self.assertEqual(st["superseded"], 1)
        self.assertEqual(st["retrievable"], st["documents"] - 2)


if __name__ == "__main__":
    unittest.main()
