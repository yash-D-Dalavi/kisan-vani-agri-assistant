import unittest

import helpers  # noqa: F401
import evaluator


class EvaluatorTests(unittest.TestCase):
    def test_targets_met_and_report_shape(self):
        rep = evaluator.run_evaluation(include_voice=True)
        self.assertGreaterEqual(rep["n_questions"], 70)
        self.assertTrue(all(t["pass"] for t in rep["targets"]), [t for t in rep["targets"] if not t["pass"]])
        self.assertEqual(rep["failures"], [])
        m = rep["metrics"]
        for k in ("recall_at_5", "faithfulness", "safe_refusal_accuracy", "latency_measured_ms", "wer_fixture", "chrf_fixture"):
            self.assertIn(k, m)
        self.assertEqual(m["voice_e2e_decision_accuracy"], 1.0)
        self.assertEqual(m["hallucination_detection_rate"], 1.0)
        cats = set(rep["per_category"])
        self.assertEqual(cats, {"answerable", "live_tool", "chemical_dosage", "low_confidence_ambiguous", "unanswerable_adversarial"})
        self.assertIn("EVALUATION SUMMARY", evaluator.format_summary(rep))


if __name__ == "__main__":
    unittest.main()
