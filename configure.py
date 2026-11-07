#!/usr/bin/env python3
"""Configure agent clients to launch Python and this checkout's MCP script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from history_agent.setup import main

    main()
