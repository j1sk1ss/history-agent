"""
Local protocol fixture. It never connects to a real project or inference
server.
"""

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ADDRESS = {
    "project_id": {
        "type": "integer",
        "minimum": 0,
        "maximum": 9223372036854775807,
    },
    "snapshot_id": {"type": "string", "minLength": 1},
    "file": {"type": "string", "minLength": 1},
    "line": {"type": "integer", "minimum": 1, "maximum": 2147483647},
}
SCOPE = {
    "project_id": ADDRESS["project_id"],
    "stream_id": {"type": "string", "minLength": 1},
    "snapshot_id": ADDRESS["snapshot_id"],
}
PAGING = {
    "offset": {
        "type": "integer",
        "minimum": 0,
        "maximum": 2147483647,
        "default": 0,
    },
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
}


def action_schema(properties, required, post=()):
    return {
        "type": "object",
        "properties": {
            name: {
                **schema,
                "x-http-parameter-location": "post" if name in post else "get",
            }
            for name, schema in properties.items()
        },
        "required": list(required),
    }


EXTENSION = {
    "agent_compare_warnings": {
        "description": "Compare current and base snapshots; HEAD is allowed.",
        "read_only": True,
        "input_schema": action_schema(
            {
                **SCOPE,
                "base_project_id": ADDRESS["project_id"],
                "base_stream_id": SCOPE["stream_id"],
                "base_snapshot_id": ADDRESS["snapshot_id"],
                "subset": {
                    "type": "choose",
                    "enum": ["appeared", "disappeared", "both"],
                    "default": "appeared",
                },
                "checker_name": {"type": "string", "minLength": 1},
                **PAGING,
            },
            (*SCOPE, "base_snapshot_id"),
        ),
    },
    "agent_find_warnings": {
        "description": "Find warnings by checker name.",
        "read_only": True,
        "input_schema": action_schema(
            {
                "project_id": {"type": "integer", "minimum": 0},
                "snapshot_id": {"type": "string", "minLength": 1},
                "stream_id": {"type": "string", "minLength": 1},
                "checker_name": {"type": "string", "minLength": 1},
                "match": {
                    "type": "choose",
                    "enum": ["exact", "contains"],
                    "default": "exact",
                },
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            ("project_id", "snapshot_id", "stream_id", "checker_name"),
        ),
    },
    "agent_checker_info": {
        "description": "Describe a checker by name.",
        "read_only": True,
        "input_schema": action_schema(
            {"checker_name": {"type": "string", "minLength": 1}},
            ("checker_name",),
        ),
    },
    "agent_source": {
        "description": "Read numbered lines from a source file in a snapshot.",
        "read_only": True,
        "input_schema": action_schema(
            {
                **ADDRESS,
                "before": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 199,
                    "default": 10,
                },
                "after": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 199,
                    "default": 10,
                },
            },
            ADDRESS,
        ),
    },
    "agent_resolve_symbol": {
        "description": "Follow DXR links at a one based line and column.",
        "read_only": True,
        "input_schema": action_schema(
            {
                **ADDRESS,
                "column": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2147483647,
                },
                "relation": {
                    "type": "choose",
                    "enum": ["definition", "declaration", "usage"],
                },
                **PAGING,
            },
            (*ADDRESS, "column", "relation"),
        ),
    },
    "agent_set_review": {
        "description": "Save a review classification and its explanation.",
        "read_only": False,
        "input_schema": action_schema(
            {
                "project_id": {"type": "integer", "minimum": 0},
                "stream_id": {"type": "string", "minLength": 1},
                "warning_id": {"type": "string", "minLength": 1},
                "classification": {
                    "type": "choose",
                    "enum": ["TP", "FP", "WF", "UC", "UD"],
                },
                "comment": {"type": "string", "minLength": 1},
            },
            (
                "project_id",
                "stream_id",
                "warning_id",
                "classification",
                "comment",
            ),
            post=("classification", "comment"),
        ),
    },
}
for metadata in EXTENSION.values():
    metadata["session_required"] = True
    metadata["constraints"] = {}
EXTENSION["agent_source"]["constraints"] = {
    "before": (
        "Lines before the address; total window must be at most 200 lines"
    ),
    "after": "Lines after the address; total window must be at most 200 lines",
}


def legacy_parameters(*names, post=(), required=("project_id", "path")):
    return {
        name: {
            "desc": name,
            "is_req": name in required,
            "type": "post" if name in post else "get",
        }
        for name in names
    }


LEGACY = {
    "actions_agent": {"desc": "Extension schemas", "params": {}},
    "actions_userfriendly": {"desc": "Extension descriptions", "params": {}},
    "get_projects": {"desc": "List project groups", "params": {}},
    "get_project_group_info": {
        "desc": "Projects in a group",
        "params": legacy_parameters(
            "project_group_id", required=("project_group_id",)
        ),
    },
    "branches": {
        "desc": "List branches",
        "params": legacy_parameters("project_id", required=("project_id",)),
    },
    "get_snapshots": {
        "desc": "List concrete snapshots",
        "params": legacy_parameters(
            "project_id",
            "stream_id",
            "number",
            required=("project_id", "stream_id"),
        ),
    },
    "get_warnings": {
        "desc": "List warnings",
        "params": legacy_parameters(
            "project_id",
            "count",
            "from",
            "extended",
            "format",
            post=("count", "from"),
        ),
    },
    "get_tokens": {
        "desc": "Get DXR tokens",
        "params": legacy_parameters("project_id", "path"),
    },
    "get_value_filter": {
        "desc": "Field catalogs",
        "params": legacy_parameters(
            "project_id",
            "stream_id",
            "snapshot_id",
            "field",
            required=("field",),
        ),
    },
    "get_warning": {
        "desc": "One warning",
        "params": legacy_parameters(
            "project_id", "stream_id", "snapshot_id", "warning_id", "format"
        ),
    },
    "get_warning_trace_and_role": {
        "desc": "Trace",
        "params": legacy_parameters(
            "project_id", "stream_id", "snapshot_id", "warning_id"
        ),
    },
    "get_all_reviews": {
        "desc": "Review history",
        "params": legacy_parameters("project_id", "stream_id", "warning_id"),
    },
    "shutdown": {"desc": "Stop server", "params": {}},
    **{
        name: {"desc": info["description"], "params": {}}
        for name, info in EXTENSION.items()
    },
}


class FakeHistory:
    def __init__(self):
        self.requests = []
        self.writes = []
        self.logins = 0
        self.auth_reject_once = False
        self.disconnect_on_write = False
        self.require_auth = True
        self.guide_html = None
        self.guide_requests = []
        self.warnings = []
        self.comparison_warnings = {}
        self.snapshots = {}
        self.reviews = {}
        self.apply_disconnected_write = True
        self.extension = copy.deepcopy(EXTENSION)
        self.legacy = copy.deepcopy(LEGACY)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                if urlsplit(self.path).path.endswith("/guide"):
                    owner.guide_requests.append(
                        {
                            "path": self.path,
                            "cookie": self.headers.get("Cookie", ""),
                        }
                    )
                    content = owner.guide_html
                    encoded = (content or "Guide unavailable").encode("utf-8")
                    self.send_response(200 if content else 404)
                    self.send_header(
                        "Content-Type", "text/html; charset=utf-8"
                    )
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                query = {
                    key: values[0]
                    for key, values in parse_qs(
                        urlsplit(self.path).query
                    ).items()
                }
                body = {
                    key: values[0]
                    for key, values in parse_qs(
                        self.rfile.read(
                            int(self.headers.get("Content-Length", "0"))
                        ).decode()
                    ).items()
                }
                action = query.get("act")
                owner.requests.append(
                    {
                        "action": action,
                        "query": query,
                        "body": body,
                        "cookie": self.headers.get("Cookie", ""),
                    }
                )
                if action == "auth":
                    owner.logins += 1
                    if body != {"user": "tester", "key": "test-secret"}:
                        self.send_json(
                            {
                                "status": "ERROR",
                                "error": "Bad credentials",
                                "authError": "1",
                            },
                            400,
                        )
                        return
                    self.send_json(
                        {"status": "OK"},
                        cookie="sid=valid; Path=/history; HttpOnly",
                    )
                    return
                if (
                    action != "get_actions"
                    and owner.require_auth
                    and "sid=valid" not in self.headers.get("Cookie", "")
                ):
                    self.send_json(
                        {
                            "status": "ERROR",
                            "error": "Session expired",
                            "authError": "1",
                        },
                        400,
                    )
                    return
                if action == "get_actions":
                    result = {"actions": owner.legacy}
                elif action in ("actions_agent", "agent_actions"):
                    if action not in owner.legacy:
                        self.send_json(
                            {"status": "ERROR", "error": "Unknown action"},
                            400,
                        )
                        return
                    result = {
                        "data": {
                            "api_version": 1,
                            "transport": "history-http",
                            "endpoint": "/history/api",
                            "action_parameter": "act",
                            "actions": owner.extension,
                        }
                    }
                elif action == "get_projects":
                    result = {
                        "project_groups": [
                            {"project_group_id": 10},
                            {"project_group_id": 20},
                        ]
                    }
                elif action == "get_project_group_info":
                    if query["project_group_id"] == "10":
                        result = {
                            "project_group": {
                                "projects": [
                                    {"project_id": 7, "project_name": "Zulu"}
                                ]
                            }
                        }
                    else:
                        result = {
                            "project_group": {
                                "projects": [
                                    {"project_id": 2, "project_name": "Alpha"}
                                ]
                            }
                        }
                elif action == "branches":
                    result = {
                        "branches": [
                            {"stream_id": "zeta"},
                            {"stream_id": "main"},
                        ]
                    }
                elif action == "get_snapshots":
                    result = {
                        "snapshots": owner.snapshots.get(
                            (int(query["project_id"]), query["stream_id"]),
                            [
                                {"snapshot_id": "snapshot-first"},
                                {"snapshot_id": "snapshot-second"},
                            ],
                        )
                    }
                elif action == "agent_source":
                    result = {
                        "data": {
                            "project_id": int(query["project_id"]),
                            "snapshot_id": query["snapshot_id"],
                            "file": query["file"],
                            "line": int(query["line"]),
                            "lines": [
                                {
                                    "number": int(query["line"]),
                                    "text": "int value = 42; // пример",
                                }
                            ],
                            "has_more": False,
                        }
                    }
                elif action == "agent_find_warnings":
                    checker = query["checker_name"].casefold()
                    found = [
                        warning
                        for warning in owner.warnings
                        if warning["checker_name"].casefold() == checker
                    ]
                    found.sort(
                        key=lambda warning: (
                            warning["location"]["file_path"],
                            warning["location"]["line"],
                            warning["warning_id"],
                        )
                    )
                    offset, limit = (
                        int(query.get("offset", 0)),
                        int(query.get("limit", 20)),
                    )
                    page_end = offset + limit
                    data = {
                        "warnings": found[offset:page_end],
                        "total": len(found),
                        "has_more": offset + limit < len(found),
                    }
                    if data["has_more"]:
                        data["next_offset"] = offset + limit
                    result = {"data": data}
                elif action == "agent_compare_warnings":
                    current = {key: query[key] for key in SCOPE}
                    current["project_id"] = int(current["project_id"])
                    base = {
                        key: query.get("base_" + key, value)
                        for key, value in current.items()
                    }
                    base["project_id"] = int(base["project_id"])
                    for scope in (current, base):
                        if scope["snapshot_id"] == "HEAD":
                            scope["snapshot_id"] = "snapshot-first"
                    subset = query.get("subset", "appeared")
                    found = owner.comparison_warnings.get(subset, [])
                    if query.get("checker_name"):
                        found = [
                            warning
                            for warning in found
                            if warning["checker_name"].casefold()
                            == query["checker_name"].casefold()
                        ]
                    offset, limit = int(query["offset"]), int(query["limit"])
                    data = {
                        "current": current,
                        "base": base,
                        "subset": subset,
                        "scope": base if subset == "disappeared" else current,
                        "warnings": found[offset : offset + limit],
                        "total": len(found),
                        "offset": offset,
                        "limit": limit,
                        "has_more": offset + limit < len(found),
                    }
                    if data["has_more"]:
                        data["next_offset"] = offset + limit
                    result = {"data": data}
                elif action == "agent_resolve_symbol":
                    result = {
                        "data": {"matches": [], "total": 0, "has_more": False}
                    }
                elif action == "get_value_filter":
                    if query["field"] == "warn_types":
                        result = {
                            "checker_groups": [
                                {
                                    "checkers": [
                                        {"checker_name": name}
                                        for name in sorted(
                                            {
                                                w["checker_name"]
                                                for w in owner.warnings
                                            }
                                        )
                                    ]
                                }
                            ]
                        }
                    else:
                        names = (
                            "Undecided",
                            "Confirmed",
                            "Won't fix",
                            "False Positive",
                            "Unclear",
                        )
                        result = {
                            "classification": [
                                {"id": i, "name": name}
                                for i, name in enumerate(names)
                            ]
                        }
                elif action == "get_warning":
                    warning = copy.deepcopy(
                        next(
                            (
                                w
                                for w in owner.warnings
                                if w["warning_id"] == query["warning_id"]
                            ),
                            None,
                        )
                    )
                    if warning is None:
                        self.send_json(
                            {"status": "ERROR", "error": "Warning not found"},
                            404,
                        )
                        return
                    warning["attributes"] = copy.deepcopy(
                        owner.reviews.get(
                            query["warning_id"],
                            {
                                "classification_id": 0,
                                "comment": "",
                                "trust_rank": -1,
                                "user_attributes": {"custom": "keep"},
                            },
                        )
                    )
                    result = {"warning": warning}
                elif action == "get_warning_trace_and_role":
                    warning = next(
                        w
                        for w in owner.warnings
                        if w["warning_id"] == query["warning_id"]
                    )
                    result = {
                        "trace": [
                            {
                                "message": "Synthetic trace",
                                "location": warning["location"],
                            }
                        ]
                    }
                elif action == "get_all_reviews":
                    result = {"reviews": []}
                elif action == "agent_checker_info":
                    result = {
                        "data": {
                            "checker_name": query["checker_name"],
                            "description": "Synthetic checker",
                        }
                    }
                elif action == "agent_set_review":
                    if owner.auth_reject_once:
                        owner.auth_reject_once = False
                        self.send_json(
                            {
                                "status": "ERROR",
                                "error": "Session expired",
                                "authError": "1",
                            },
                            400,
                        )
                        return
                    owner.writes.append({**query, **body})
                    if (
                        not owner.disconnect_on_write
                        or owner.apply_disconnected_write
                    ):
                        ids = {"UD": 0, "TP": 1, "WF": 2, "FP": 3, "UC": 4}
                        previous = copy.deepcopy(
                            owner.reviews.get(
                                query["warning_id"],
                                {
                                    "classification_id": 0,
                                    "comment": "",
                                    "trust_rank": -1,
                                    "user_attributes": {"custom": "keep"},
                                },
                            )
                        )
                        previous.update(
                            classification_id=ids[body["classification"]],
                            comment=body["comment"],
                        )
                        owner.reviews[query["warning_id"]] = previous
                    if owner.disconnect_on_write:
                        self.close_connection = True
                        return
                    result = {
                        "data": {
                            "classification": body["classification"],
                            "comment": body["comment"],
                        }
                    }
                elif action == "get_warnings":
                    result = {"warnings": [], "remaining_warnings": 0}
                else:
                    result = {"tokens": []}
                self.send_json({"status": "OK", **result})

            def send_json(self, result, status=200, cookie=None):
                encoded = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
