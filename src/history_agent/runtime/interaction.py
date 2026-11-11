"""Host-side input for one suspended agent question."""

import asyncio
import sys


class UserInteraction:
    @staticmethod
    async def answer(ui, question: str, options: list[str]) -> str | None:
        if ui and hasattr(ui, "ask_user"):
            return await ui.ask_user(question, options)
        if not sys.stdin.isatty():
            return None
        live = getattr(ui, "live", None)
        if live:
            live.stop()
        try:
            print("\nAgent: " + question, file=sys.stderr)
            for index, option in enumerate(options, 1):
                print(f"  {index}. {option}", file=sys.stderr)
            value = await asyncio.to_thread(input, "answer> ")
            if value.isdigit() and 1 <= int(value) <= len(options):
                return options[int(value) - 1]
            return value
        except EOFError:
            return None
        finally:
            if live:
                live.start(refresh=True)
