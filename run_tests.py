import os
import sys
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
