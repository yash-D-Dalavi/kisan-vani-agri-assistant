import unittest

import helpers  # noqa: F401
import eval_metrics as M


class MetricTests(unittest.TestCase):
    def test_wer_known_values(self):
        self.assertAlmostEqual(M.wer(["a b c d"], ["a b c d"]), 0.0)
        self.assertAlmostEqual(M.wer(["a b c d"], ["a x c"]), 0.5)            # 1 substitution + 1 deletion over 4 words
        self.assertAlmostEqual(M.wer(["a b", "c d"], ["a b", "c x"]), 0.25)    # corpus level

    def test_cer(self):
        self.assertAlmostEqual(M.cer(["abcd"], ["abxd"]), 0.25)

    def test_chrf_bleu(self):
        self.assertAlmostEqual(M.chrf("कपास में कीड़े", "कपास में कीड़े"), 100.0, places=3)
        self.assertLess(M.chrf("बिल्कुल अलग वाक्य", "कपास में कीड़े"), 30)
        self.assertGreater(M.bleu("the cat sat on the mat", "the cat sat on the mat"), 90)
        self.assertLess(M.bleu("dog", "the cat sat on the mat"), 20)

    def test_retrieval_metrics(self):
        self.assertEqual(M.recall_at_k(["a", "b", "c"], ["c"], 2), 0.0)
        self.assertEqual(M.recall_at_k(["a", "b", "c"], ["c"], 3), 1.0)
        self.assertAlmostEqual(M.reciprocal_rank(["a", "b", "c"], ["b"]), 0.5)

    def test_percentile(self):
        self.assertEqual(M.percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertAlmostEqual(M.percentile([10, 20], 95), 19.5)


if __name__ == "__main__":
    unittest.main()
