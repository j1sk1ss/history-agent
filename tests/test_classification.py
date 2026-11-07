import unittest

from history_agent.classification import (
    EVIDENCE_REQUIRED,
    PATTERN_CLASSES,
    REVIEW_CRITERIA,
    STATUSES,
)


class ClassificationPolicyTest(unittest.TestCase):
    def test_wf_is_distinct_from_false_positive(self):
        self.assertEqual(STATUSES["WF"], "Won't fix")
        self.assertIn("reported condition does not hold", REVIEW_CRITERIA)
        self.assertIn("reported condition does hold", REVIEW_CRITERIA)
        self.assertIn(
            "WF does not mean the detector is wrong", REVIEW_CRITERIA
        )
        self.assertIn("TP", EVIDENCE_REQUIRED)
        self.assertIn("FP", EVIDENCE_REQUIRED)
        self.assertIn("WF", EVIDENCE_REQUIRED)
        self.assertIn("WF", PATTERN_CLASSES)


if __name__ == "__main__":
    unittest.main()
