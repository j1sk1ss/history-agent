import unittest

from history_agent.history.client import HistoryClient, HistoryError, Settings
from history_agent.history.guide import Guide, GuideError
from history_agent.history.schemas import DOC_ACTION
from tests.fake_history import FakeHistory

HTML = """<!doctype html><html><head><title>Synthetic Svace guide</title>
<style>hidden CSS</style></head><body>
<nav id="TOC"><h3 id="CHECKER.ONE">Fake TOC</h3></nav>
<h2 id="warnings">Warnings</h2>
<h3 id="CHECKER.ONE">CHECKER.ONE</h3>
<p>The rule checks a synthetic condition.</p>
<h4>Example</h4><pre><code>void example() {
    target = source; // пример
}</code></pre>
<h3 id="CHECKER.ONE.NEXT">CHECKER.ONE.NEXT</h3><p>Different rule.</p>
<h2 id="other">Other sections</h2><p>Unrelated chapter.</p></body></html>"""


class GuideTest(unittest.TestCase):
    def test_exact_article_includes_examples_but_not_other_checkers_or_toc(
        self,
    ):
        guide = Guide("https://history.example/prefix/guide", lambda: HTML)
        result = guide.checker("checker.one")
        self.assertEqual(result["checker_name"], "CHECKER.ONE")
        self.assertEqual(result["guide_title"], "Synthetic Svace guide")
        self.assertEqual(
            result["source_url"],
            "https://history.example/prefix/guide#CHECKER.ONE",
        )
        self.assertIn("target = source; // пример", result["text"])
        self.assertIn("\n    target = source; // пример\n", result["text"])
        for absent in (
            "hidden CSS",
            "Fake TOC",
            "Different rule.",
            "Unrelated chapter.",
            "CHECKER.ONE.NEXT",
        ):
            self.assertNotIn(absent, result["text"])
        with self.assertRaises(GuideError):
            guide.checker("CHECKER.ONE.UNKNOWN")

    def test_paging_reconstructs_article_and_guide_stays_outside_model_context(
        self,
    ):
        loads = []

        def load():
            loads.append(True)
            return HTML

        guide = Guide("https://history.example/guide", load)
        full = guide.checker("CHECKER.ONE")["text"]
        parts, offset = [], 0
        while True:
            result = guide.checker("CHECKER.ONE", offset, 29)
            parts.append(result["text"])
            if not result["has_more"]:
                break
            offset = result["next_offset"]
        self.assertEqual("".join(parts), full)
        guide.checker("CHECKER.ONE.NEXT")
        self.assertEqual(len(loads), 1)

    def test_client_enriches_checker_info_and_exposes_paginated_local_action(
        self,
    ):
        with FakeHistory() as server:
            server.guide_html = HTML
            client = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            info = client.execute(
                "agent_checker_info", {"checker_name": "CHECKER.ONE"}
            )
            self.assertEqual(info["documentation"]["status"], "available")
            self.assertIn("synthetic condition", info["documentation"]["text"])
            self.assertEqual(info["description"], "Synthetic checker")
            page = client.execute(
                DOC_ACTION, {"checker_name": "CHECKER.ONE", "limit": 20}
            )
            self.assertTrue(page["has_more"])
            self.assertEqual(len(page["text"]), 20)
            self.assertEqual(len(server.guide_requests), 1)
            # The fixture's cookie is scoped to /history; /guide must not widen
            # that scope.
            self.assertEqual(server.guide_requests[0]["cookie"], "")
            self.assertIn(
                DOC_ACTION,
                [action["name"] for action in client.discover()["actions"]],
            )
            self.assertFalse(
                any(
                    request["action"] == DOC_ACTION
                    for request in server.requests
                )
            )

    def test_unavailable_guide_keeps_catalog_metadata_honest(self):
        with FakeHistory() as server:
            client = HistoryClient(
                Settings(server.url, "tester", "test-secret")
            )
            info = client.execute(
                "agent_checker_info", {"checker_name": "CHECKER.ONE"}
            )
            self.assertEqual(info["description"], "Synthetic checker")
            self.assertEqual(info["documentation"]["status"], "unavailable")
            with self.assertRaises(HistoryError):
                client.execute(DOC_ACTION, {"checker_name": "CHECKER.ONE"})
            self.assertEqual(len(server.guide_requests), 1)
            with self.assertRaises(HistoryError):
                client.execute(
                    DOC_ACTION, {"checker_name": "CHECKER.ONE"}, write=True
                )

    def test_remote_deployment_prefix_and_schema_validation(self):
        with FakeHistory() as server:
            server.guide_html = HTML
            client = HistoryClient(
                Settings(
                    server.url + "/deployment/history/api", session="valid"
                )
            )
            for args in (
                {},
                {"checker_name": "CHECKER.ONE", "limit": 8001},
                {"checker_name": "CHECKER.ONE", "offset": -1},
            ):
                with self.assertRaises(HistoryError):
                    client.execute(DOC_ACTION, args)
            self.assertFalse(server.guide_requests)
            article = client.execute(
                DOC_ACTION, {"checker_name": "CHECKER.ONE"}
            )
            self.assertEqual(
                server.guide_requests[0]["path"], "/deployment/guide"
            )
            self.assertEqual(
                article["source_url"],
                server.url + "/deployment/guide#CHECKER.ONE",
            )


if __name__ == "__main__":
    unittest.main()
