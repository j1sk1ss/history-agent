#!/usr/bin/env python3
"""Run the shared history MCP server directly from this checkout."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from history_agent.server import main
    main()
