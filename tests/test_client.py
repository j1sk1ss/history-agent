import unittest
from unittest.mock import patch

from history_agent.history.client import HistoryClient, HistoryError, Settings
from tests.fake_history import FakeHistory

SOURCE = {
    "project_id": 0,
    "snapshot_id": "snapshot",
    "file": "src/a & b.c",
    "line": 3,
}
REVIEW = {
    "project_id": 0,
    "stream_id": "master",
    "warning_id": "warning",
    "classification": "TP",
    "comment": "Evidence at src/a.c:3 & explanation",
}


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHistory().__enter__()
        self.addCleanup(self.fake.__exit__)
        self.client = HistoryClient(
            Settings(self.fake.url, "tester", "test-secret")
        )

    def test_discovery_is_small_and_schema_is_loaded_on_demand(self):
        catalog = self.client.discover()
        self.assertNotIn(
            "shutdown", [action["name"] for action in catalog["actions"]]
        )
        self.assertTrue(
            all("input_schema" not in action for action in catalog["actions"])
        )
        schema = self.client.discover("agent_source")["input_schema"]
        self.assertEqual(schema["properties"]["line"]["type"], "integer")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(self.fake.logins, 1)
        self.assertEqual(
            len(
                [r for r in self.fake.requests if r["action"] == "get_actions"]
            ),
            1,
        )

    def test_read_uses_cookie_defaults_and_url_encoding(self):
        result = self.client.execute("agent_source", SOURCE)
        self.assertEqual(result["file"], SOURCE["file"])
        self.assertIn("пример", result["lines"][0]["text"])
        request = self.fake.requests[-1]
        self.assertEqual(request["query"]["before"], "10")
        self.assertEqual(request["cookie"], "sid=valid")

    def test_legacy_warning_page_is_bounded_and_filters_use_form_fields(self):
        self.client.execute(
            "get_warnings", {"project_id": 0, "from": 5, "extended": True}
        )
        request = self.fake.requests[-1]
        self.assertEqual(request["query"]["extended"], "true")
        self.assertEqual(request["body"], {"count": "20", "from": "5"})
        self.assertNotIn("count", request["query"])

    def test_legacy_token_path_documentation_is_corrected(self):
        self.client.execute("get_tokens", {"project_id": 0, "file": "a.c"})
        self.assertEqual(self.fake.requests[-1]["query"]["file"], "a.c")
        self.assertNotIn("path", self.fake.requests[-1]["query"])

    def test_checker_name_actions_are_discovered_validated_and_read_only(self):
        args = {
            "project_id": 0,
            "stream_id": "master",
            "snapshot_id": "snapshot",
            "checker_name": "NULL",
        }
        self.client.execute("agent_find_warnings", args)
        request = self.fake.requests[-1]
        self.assertEqual(request["query"]["match"], "exact")
        self.assertEqual(request["query"]["limit"], "20")
        self.assertEqual(request["body"], {})
        self.assertEqual(
            self.client.execute(
                "agent_checker_info", {"checker_name": "NULL"}
            )["checker_name"],
            "NULL",
        )
        with self.assertRaises(HistoryError):
            self.client.execute("agent_find_warnings", {**args, "limit": 101})
        with self.assertRaises(HistoryError):
            self.client.execute(
                "agent_checker_info", {"checker_name": "NULL"}, write=True
            )

    def test_review_posts_only_review_fields_and_encodes_comment(self):
        saved = self.client.execute("agent_set_review", REVIEW, write=True)
        request = self.fake.requests[-1]
        self.assertEqual(
            request["body"],
            {"classification": "TP", "comment": REVIEW["comment"]},
        )
        self.assertEqual(saved["comment"], REVIEW["comment"])
        self.assertEqual(len(self.fake.writes), 1)

    def test_tool_separation_does_not_trust_server_readonly_flag(self):
        self.fake.extension["agent_set_review"]["read_only"] = True
        with self.assertRaises(HistoryError):
            self.client.execute("agent_set_review", REVIEW)
        with self.assertRaises(HistoryError):
            self.client.execute("agent_source", SOURCE, write=True)
        with self.assertRaises(HistoryError):
            self.client.execute("shutdown", {})
        self.assertEqual(self.fake.requests, [])

    def test_unknown_or_invalid_arguments_never_reach_handler(self):
        self.client.discover()
        before = len(self.fake.requests)
        for arguments in (
            {**SOURCE, "act": "shutdown"},
            {**SOURCE, "line": 0},
            {**SOURCE, "line": True},
            {
                key: value
                for key, value in SOURCE.items()
                if key != "snapshot_id"
            },
        ):
            with self.assertRaises(HistoryError):
                self.client.execute("agent_source", arguments)
        with self.assertRaises(HistoryError):
            self.client.execute(
                "agent_set_review",
                {**REVIEW, "classification": "WRONG"},
                write=True,
            )
        self.assertEqual(len(self.fake.requests), before)

    def test_explicit_auth_rejection_can_reauthenticate_before_write(self):
        self.client.discover()
        self.fake.auth_reject_once = True
        self.client.execute("agent_set_review", REVIEW, write=True)
        self.assertEqual(self.fake.logins, 2)
        self.assertEqual(len(self.fake.writes), 1)

    def test_ambiguous_write_failure_is_not_retried(self):
        self.fake.disconnect_on_write = True
        with self.assertRaisesRegex(HistoryError, "outcome may be unknown"):
            self.client.execute("agent_set_review", REVIEW, write=True)
        self.assertEqual(len(self.fake.writes), 1)

    def test_existing_session_can_be_used_without_password(self):
        client = HistoryClient(Settings(self.fake.url, session="valid"))
        client.execute("agent_source", SOURCE)
        self.assertEqual(self.fake.logins, 0)

    def test_old_server_can_still_expose_legacy_read_operations(self):
        original = self.client._call

        def without_extension(action, *args, **kwargs):
            result = original(action, *args, **kwargs)
            if action == "get_actions":
                result["actions"].pop("actions_agent")
                result["actions"].pop("actions_userfriendly")
            return result

        with patch.object(self.client, "_call", side_effect=without_extension):
            catalog = self.client.discover()
            self.assertIn(
                "get_projects", [item["name"] for item in catalog["actions"]]
            )
            self.assertNotIn(
                "agent_set_review",
                [item["name"] for item in catalog["actions"]],
            )
            self.assertNotIn(
                "agent_source", [item["name"] for item in catalog["actions"]]
            )

    def test_credential_urls_and_incomplete_credentials_are_rejected(self):
        for settings in (
            Settings("http://user:secret@localhost"),
            Settings(self.fake.url, "tester"),
        ):
            with self.assertRaises(ValueError):
                HistoryClient(settings)


if __name__ == "__main__":
    unittest.main()
