"""Response validation must match the schema supplied to the model."""

import unittest

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from history_agent.review.models import ReviewPattern, ReviewVerdict


class ReviewModelsTest(unittest.TestCase):
    def verdict(self) -> dict[str, object]:
        return {
            "classification": "TP",
            "comment": "The guard is missing.",
            "evidence": [
                {"file": "src/main.c", "line": 3, "snapshot_id": "revision"}
            ],
        }

    def test_schema_and_serialization_agree(self) -> None:
        value: dict[str, object] = self.verdict()
        value["pattern"] = {
            "name": "missing-guard",
            "criterion": "Guard before access",
            "checks": ["Read the preceding branch"],
        }
        parsed: ReviewVerdict = ReviewVerdict.model_validate(value)
        self.assertEqual(parsed.evidence[0].line, 3)
        self.assertIsInstance(parsed.pattern, ReviewPattern)
        self.assertEqual(parsed.model_dump(exclude_none=True), value)
        Draft202012Validator(ReviewVerdict.model_json_schema()).validate(value)

    def test_nested_evidence_rejects_coercion_and_unknown_fields(self) -> None:
        for line in (True, "3", 3.0, 0):
            with self.subTest(line=line), self.assertRaises(ValidationError):
                value: dict[str, object] = self.verdict()
                value["evidence"][0]["line"] = line
                ReviewVerdict.model_validate(value)
        value = self.verdict()
        value["evidence"][0]["unexpected"] = "ignored?"
        with self.assertRaises(ValidationError):
            ReviewVerdict.model_validate(value)

    def test_rejects_blank_comment_and_invalid_verdict_fields(self) -> None:
        invalid: list[dict[str, object]] = [
            {"comment": " \n\t"},
            {"comment": "x" * 8001},
            {"classification": "unknown"},
            {"evidence": [{}]},
            {"evidence": self.verdict()["evidence"] * 21},
            {"extra": True},
        ]
        for fields in invalid:
            with (
                self.subTest(fields=fields),
                self.assertRaises(ValidationError),
            ):
                ReviewVerdict.model_validate({**self.verdict(), **fields})

    def test_pattern_bounds_apply_to_each_check(self) -> None:
        for checks in ([], [""], ["x" * 161], ["Check"] * 5, [123]):
            with (
                self.subTest(checks=checks),
                self.assertRaises(ValidationError),
            ):
                ReviewPattern.model_validate(
                    {
                        "name": "guard",
                        "criterion": "Guard exists",
                        "checks": checks,
                    }
                )
