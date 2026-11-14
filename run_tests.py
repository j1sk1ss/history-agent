import os
import sys
import tempfile
import unittest
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    source = str(root / "src")
    sys.path.insert(0, source)
    os.environ["PYTHONPATH"] = source + (
        os.pathsep + os.environ["PYTHONPATH"]
        if os.environ.get("PYTHONPATH")
        else ""
    )
    # Never connect tests to a developer's Redis database.
    os.environ["HISTORY_REDIS_URL"] = ""
    with tempfile.TemporaryDirectory(prefix="history-test-config-") as config:
        # Subprocess CLI tests must never load the developer's saved endpoints.
        os.environ["HISTORY_AGENT_CONFIG"] = str(Path(config) / "config.json")
        unittest.main(
            module=None,
            argv=[
                sys.argv[0],
                "discover",
                "-s",
                str(root / "tests"),
                "-t",
                str(root),
                *sys.argv[1:],
            ],
        )
