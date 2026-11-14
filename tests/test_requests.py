"""Local request models replace handwritten schemas and conditional maps."""

import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from history_agent.contracts import ActionDefinition, ActionMetadata
from history_agent.history.actions import (
    CHECKER_DOCUMENTATION,
    COMPARE_WARNINGS,
    CURRENT,
    RESOLVE_URL,
)
from history_agent.history.client import HistoryClient, HistoryError, Settings
from history_agent.history.requests import (
    CheckerDocumentationRequest,
    CompareWarningsRequest,
)
from history_agent.review.actions import BATCH_REVIEW, DIFFERENCE_REVIEW
from history_agent.review.requests import (
    BatchCommand,
    BatchRequest,
    BatchSelection,
    DifferenceCommand,
    DifferenceSelection,
    ResolvedBatchCommand,
    ResumeRequest,
)

SCOPE: dict[str, str | int] = {
    "project_id": 0,
    "stream_id": "master",
    "snapshot_id": "current",
}


class RequestModelsTest(unittest.TestCase):
    def test_every_local_action_uses_a_model(self) -> None:
        definitions: tuple[ActionDefinition, ...] = (
            CURRENT,
            RESOLVE_URL,
            CHECKER_DOCUMENTATION,
            COMPARE_WARNINGS,
            BATCH_REVIEW,
            DIFFERENCE_REVIEW,
        )
        for definition in definitions:
            with self.subTest(action=definition.name):
                metadata: ActionMetadata = definition.metadata()
                self.assertEqual(metadata.name, definition.name)
                self.assertEqual(
                    metadata.input_schema,
                    definition.request_model.model_json_schema(),
                )
                Draft202012Validator.check_schema(metadata.input_schema)

    def test_discovery_returns_independent_serialized_metadata(self) -> None:
        client: HistoryClient = HistoryClient(Settings("http://synthetic"))
        with patch.object(client, "_call", return_value={"actions": {}}):
            first: dict[str, object] = client.discover(CURRENT.name)
            first["input_schema"]["properties"].clear()
            second: dict[str, object] = client.discover(CURRENT.name)
        self.assertIn("project_id", second["input_schema"]["properties"])

    def test_local_client_validates_and_applies_model_defaults(self) -> None:
        client: HistoryClient = HistoryClient(Settings("http://synthetic"))
        with patch.object(client, "_call", return_value={"actions": {}}):
            schema: dict[str, object]
            values: dict[str, object]
            schema, values = client.prepare_arguments(
                CHECKER_DOCUMENTATION.name, {"checker_name": "CHECKER"}
            )
            self.assertEqual(
                schema, CheckerDocumentationRequest.model_json_schema()
            )
            self.assertEqual(
                values,
                {
                    "checker_name": "CHECKER",
                    "offset": 0,
                    "limit": 3000,
                },
            )
            for invalid in (
                {},
                {"checker_name": "CHECKER", "offset": True},
                {"checker_name": "CHECKER", "limit": 8001},
                {"checker_name": "CHECKER", "extra": True},
            ):
                with self.subTest(arguments=invalid):
                    with self.assertRaises(HistoryError):
                        client.prepare_arguments(
                            CHECKER_DOCUMENTATION.name, invalid
                        )

    def test_public_scope_resolution_and_runner_requirements_differ(
        self,
    ) -> None:
        public: BatchSelection = BatchCommand.model_validate({}).root
        self.assertIsInstance(public, BatchSelection)
        self.assertEqual(public.model_dump(exclude_unset=True), {})
        with self.assertRaises(ValidationError):
            ResolvedBatchCommand.model_validate({})
        resolved: BatchRequest = ResolvedBatchCommand.model_validate(
            SCOPE
        ).root
        self.assertIsInstance(resolved, BatchRequest)
        self.assertEqual(resolved.instructions, "")
        self.assertFalse(resolved.overwrite)

    def test_resume_accepts_only_job_id(self) -> None:
        for command in (BatchCommand, ResolvedBatchCommand, DifferenceCommand):
            with self.subTest(command=command.__name__):
                resume: ResumeRequest = command.model_validate(
                    {"job_id": "a" * 32}
                ).root
                self.assertIsInstance(resume, ResumeRequest)
                for invalid in (
                    {"job_id": "../escape"},
                    {"job_id": "a" * 32, "instructions": "new goal"},
                    {"job_id": "a" * 32, **SCOPE},
                ):
                    with self.assertRaises(ValidationError):
                        command.model_validate(invalid)

    def test_difference_requires_both_snapshots(self) -> None:
        with self.assertRaises(ValidationError):
            DifferenceCommand.model_validate(SCOPE)
        request: DifferenceSelection = DifferenceCommand.model_validate(
            {
                **SCOPE,
                "base_snapshot_id": "base",
            }
        ).root
        self.assertIsInstance(request, DifferenceSelection)
        self.assertEqual(request.subset, "appeared")
        self.assertEqual(request.base_snapshot_id, "base")

    def test_public_reviews_accept_head_before_runner_scope_resolution(self):
        batch = BatchCommand.model_validate({"snapshot_id": "HEAD"}).root
        self.assertEqual(batch.snapshot_id, "HEAD")
        arguments = {
            **SCOPE, "snapshot_id": "HEAD", "base_snapshot_id": "HEAD",
        }
        difference = DifferenceCommand.model_validate(arguments).root
        self.assertEqual(difference.snapshot_id, "HEAD")
        self.assertEqual(difference.base_snapshot_id, "HEAD")
        self.assertTrue(Draft202012Validator(
            DifferenceCommand.model_json_schema()
        ).is_valid(arguments))
        with self.assertRaises(ValidationError):
            ResolvedBatchCommand.model_validate(arguments)

    def test_comparison_options_require_a_base_snapshot(self) -> None:
        for fields in (
            {"base_project_id": 1},
            {"base_stream_id": "other"},
            {"subset": "both"},
        ):
            for command in (BatchCommand, ResolvedBatchCommand):
                with self.subTest(command=command.__name__, fields=fields):
                    with self.assertRaises(ValidationError):
                        command.model_validate({**SCOPE, **fields})

    def test_request_types_bounds_and_concrete_snapshots(self) -> None:
        invalid: list[dict[str, object]] = [
            {"project_id": True},
            {"project_id": "0"},
            {"project_id": -1},
            {"stream_id": ""},
            {"snapshot_id": "HEAD"},
            {"snapshot_id": ""},
            {"overwrite": "false"},
            {"instructions": "x" * 8001},
            {"base_snapshot_id": "HEAD"},
            {"unrecognized": True},
        ]
        for fields in invalid:
            with (
                self.subTest(fields=fields),
                self.assertRaises(ValidationError),
            ):
                ResolvedBatchCommand.model_validate({**SCOPE, **fields})
        for fields in (
            {"offset": -1},
            {"limit": 0},
            {"limit": 101},
            {"limit": True},
            {"subset": "unknown"},
        ):
            with (
                self.subTest(fields=fields),
                self.assertRaises(ValidationError),
            ):
                CompareWarningsRequest.model_validate(
                    {
                        **SCOPE,
                        "base_snapshot_id": "base",
                        **fields,
                    }
                )

    def test_generated_schema_also_rejects_head(self) -> None:
        schema: dict[str, object] = CompareWarningsRequest.model_json_schema()
        validator: Draft202012Validator = Draft202012Validator(schema)
        self.assertTrue(
            validator.is_valid(
                {
                    **SCOPE,
                    "base_snapshot_id": "base",
                }
            )
        )
        self.assertFalse(
            validator.is_valid(
                {
                    **SCOPE,
                    "snapshot_id": "HEAD",
                    "base_snapshot_id": "base",
                }
            )
        )
