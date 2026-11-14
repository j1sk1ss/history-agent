"""
Local slash commands and terminal completion; nothing is sent to the model.
"""

from __future__ import annotations

from pathlib import Path

COMMANDS: tuple[str, ...] = (
    "/help",
    "/config",
    "/reset",
    "/reload",
    "/batch",
    "/review",
    "/diff",
    "/resume",
    "/quit",
    "/quite",
)
HELP: str = """Commands (Tab completes names; ↑/↓ recall this session's input):
  /help                               Show this help
  /config                             Open settings in the terminal UI
                                      (or launch with --configure)
  /reset                              Start with fresh model context
  /reload                             Retry the last request or stopped job
  /batch [PROJECT BRANCH SNAPSHOT [CHECKER]]
                                      Review warnings (defaults via current)
  /review PROJECT BRANCH SNAPSHOT WARNING
                                      Review one warning
  /diff PROJECT BRANCH CURRENT BASE [appeared|disappeared|both] [CHECKER]
                                      Review a difference (default: appeared)
  /resume JOB_ID                      Resume a saved batch (Tab completes IDs)
  /quit                               Exit (/quite also works)
Snapshot arguments accept HEAD; reviews pin it before starting.
Validated batch reviews are saved immediately in live mode.
Use --read-only to keep results local."""


def install_completion(state_dir: str | Path) -> None:
    try:
        import readline
    except ImportError:
        return

    root: Path = Path(state_dir)

    def complete(text: str, state: int) -> str | None:
        try:
            before: str = readline.get_line_buffer()[: readline.get_begidx()]
            if not before.strip():
                matches: list[str] = [
                    command for command in COMMANDS if command.startswith(text)
                ]
            elif before.split() == ["/resume"]:
                matches = sorted(
                    path.stem
                    for path in root.glob("*.sqlite3")
                    if len(path.stem) == 32
                    and all(char in "0123456789abcdef" for char in path.stem)
                    and path.stem.startswith(text)
                )
            elif before.split()[:1] == ["/diff"] and len(before.split()) == 5:
                matches = [
                    subset
                    for subset in ("appeared", "disappeared", "both")
                    if subset.startswith(text)
                ]
            else:
                matches = []
            return matches[state] if state < len(matches) else None
        except OSError:
            return None

    readline.set_completer_delims(" \t\n")
    readline.set_completer(complete)
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set show-all-if-ambiguous on")
