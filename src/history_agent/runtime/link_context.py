"""Resolve user URLs before inference and replace stale selection context."""

import asyncio

from history_agent.history.client import HistoryError
from history_agent.history.constants import URL_ACTION
from history_agent.history.links import find_history_urls
from history_agent.inference.model import ChatError
from history_agent.prompts import Prompts


async def prepare_links(conversation, text):
    urls = find_history_urls(text)
    if len(urls) > 4:
        raise ChatError("Send at most four history links per request")
    if not urls:
        return []
    links = {}
    # All links must resolve successfully before changing the selection.
    for url in urls:
        try:
            links[url] = await asyncio.to_thread(
                conversation.history.execute,
                URL_ACTION,
                {"url": url},
            )
        except HistoryError as error:
            raise ChatError(str(error)) from None
    if conversation.router.link_scope.bind(links):
        conversation.harness.reset()
        conversation.harness.messages[0]["content"] += Prompts.get(
            "runtime.batch_hint"
        )
        conversation.router.completed = None
        conversation.runner.last_summary = None
    if conversation.ui:
        for resolved in links.values():
            conversation.ui.event(
                "history_link",
                kind_name=resolved["kind"],
                scope=resolved["scope"],
                subset=resolved.get("subset"),
            )
    return [(URL_ACTION, {"url": url}, data) for url, data in links.items()]
