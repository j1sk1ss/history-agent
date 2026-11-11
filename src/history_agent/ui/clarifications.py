"""Present an agent question while its current investigation is suspended."""

from history_agent.ui.widgets import CommandInput


async def show_question(ui, data) -> None:
    options = "\n".join(
        f"{index}. {text}"
        for index, text in enumerate(data.get("options", []), 1)
    )
    await ui.add_card(
        "Agent question",
        data["question"] + ("\n" + options if options else ""),
    )
    ui.waiting = True
    ui.query_one(CommandInput).disabled = False
    ui.query_one(CommandInput).focus()
