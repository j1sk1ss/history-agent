import unittest

from history_agent.current import resolve_current


class ExampleClient:
    def __init__(self):
        self.calls = []
        self.data = {
            "get_projects": {
                "project_groups": [
                    {"project_group_id": 10},
                    {"project_group_id": 20},
                ]
            },
            "get_project_group_info": {
                10: {
                    "project_group": {
                        "projects": [{"project_name": "Zulu", "project_id": 7}]
                    }
                },
                20: {
                    "project_group": {
                        "projects": [
                            {"project_name": "Alpha", "project_id": 2},
                            {"project_name": "Beta", "project_id": 3},
                        ]
                    }
                },
            },
            "branches": {
                2: {
                    "branches": [{"stream_id": "zeta"}, {"stream_id": "main"}]
                },
                3: {"branches": ["production"]},
                7: {"branches": ["main"]},
            },
            "get_snapshots": {
                (2, "main"): {
                    "snapshots": [
                        {"snapshot_id": "snapshot-first"},
                        {"snapshot_id": "snapshot-second"},
                    ]
                },
                (3, "production"): {
                    "snapshots": [{"snapshot_id": "beta-head"}]
                },
                (7, "main"): {"snapshots": [{"snapshot_id": "zulu-head"}]},
            },
        }

    def execute(self, action, args):
        self.calls.append((action, args))
        if action == "get_project_group_info":
            return self.data[action][args["project_group_id"]]
        if action == "branches":
            return self.data[action][args["project_id"]]
        if action == "get_snapshots":
            return self.data[action][(args["project_id"], args["stream_id"])]
        return self.data[action]


class CurrentTest(unittest.TestCase):
    def test_defaults_sort_project_and_branch_and_keep_first_snapshot(self):
        client = ExampleClient()
        selection = resolve_current(client, {}, ValueError)
        self.assertEqual(
            selection["scope"],
            {
                "project_id": 2,
                "stream_id": "main",
                "snapshot_id": "snapshot-first",
            },
        )
        self.assertEqual(selection["project_name"], "Alpha")
        self.assertEqual(
            client.calls[-1],
            (
                "get_snapshots",
                {"project_id": 2, "stream_id": "main", "number": 1},
            ),
        )

    def test_explicit_project_and_branch_are_respected(self):
        client = ExampleClient()
        selection = resolve_current(
            client, {"project_id": 7, "stream_id": "main"}, ValueError
        )
        self.assertEqual(selection["scope"]["snapshot_id"], "zulu-head")
        self.assertFalse(
            any(action == "branches" for action, _ in client.calls)
        )

    def test_named_project_is_exact_and_unknown_not_silently_replaced(self):
        client = ExampleClient()
        self.assertEqual(
            resolve_current(client, {"project_name": "Beta"}, ValueError)[
                "scope"
            ]["project_id"],
            3,
        )
        with self.assertRaisesRegex(ValueError, "No matching"):
            resolve_current(client, {"project_name": "Unknown"}, ValueError)

    def test_no_snapshot_refuses_head_or_empty(self):
        client = ExampleClient()
        client.data["get_snapshots"][(2, "main")] = {
            "snapshots": [{"snapshot_id": "HEAD"}]
        }
        with self.assertRaisesRegex(ValueError, "concrete"):
            resolve_current(client, {}, ValueError)

    def test_single_project_fallback(self):
        client = ExampleClient()
        client.data["get_projects"] = {"project_groups": []}
        client.data["branches"][0] = {"branches": ["master"]}
        client.data["get_snapshots"][(0, "master")] = {
            "snapshots": [{"snapshot_id": "solo"}]
        }
        self.assertEqual(
            resolve_current(client, {}, ValueError)["scope"]["project_id"], 0
        )


if __name__ == "__main__":
    unittest.main()
