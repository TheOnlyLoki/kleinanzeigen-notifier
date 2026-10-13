"""
Unit tests for picking watches by (part of a) name in /poll, /pull and /delete.

Run with:
  pytest tests/test_poll_command.py -v
"""

import asyncio

from notifier.commands import TelegramCommandHandler
from notifier.config import WatchConfig

SUBARU_URL = "https://www.kleinanzeigen.de/s-autos/subaru/k0c216+autos.marke_s:subaru"


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append(text)


class FakeService:
    def __init__(self, watches):
        self.watches = list(watches)
        self.polled = []
        self.removed = []

    def list_watches(self):
        return list(self.watches)

    async def poll_watch(self, name):
        self.polled.append(name)
        return []

    async def remove_watch(self, name):
        self.removed.append(name)
        self.watches = [w for w in self.watches if w.name != name]
        return True


WATCHES = [
    WatchConfig(name="Subaru Impreza", query="subaru impreza"),
    WatchConfig(name="Subaru Forester", query="forester"),
    WatchConfig(name="BMW E46", query="bmw e46"),
    WatchConfig(name="Japaner", url=SUBARU_URL),
    WatchConfig(name="Kombi", query="volvo v70"),
]


def _run(*texts, watches=WATCHES):
    service = FakeService(watches)
    telegram = FakeTelegram()
    handler = TelegramCommandHandler(service, telegram, "1")

    async def go():
        for i, text in enumerate(texts):
            await handler._handle_update(
                {"update_id": i, "message": {"chat": {"id": 1}, "text": text}}
            )
            if handler._poll_task:
                await handler._poll_task

    asyncio.run(go())
    return service, telegram


def test_pull_alias_with_partial_name_polls_all_matches():
    service, telegram = _run("/pull subaru")
    assert service.polled == ["Subaru Impreza", "Subaru Forester"]
    assert telegram.sent[0] == "\U0001F504 Polling 'Subaru Impreza', 'Subaru Forester' now..."


def test_exact_name_beats_partial_matches():
    service, _ = _run("/poll subaru impreza")
    assert service.polled == ["Subaru Impreza"]


def test_number_and_case_insensitive_name():
    service, _ = _run("/poll 3", "/pull bmw")
    assert service.polled == ["BMW E46", "BMW E46"]


def test_falls_back_to_search_keywords_and_url_filters():
    service, _ = _run("/pull volvo")
    assert service.polled == ["Kombi"]

    watches = [WatchConfig(name="Japaner", url=SUBARU_URL), WatchConfig(name="Kombi")]
    service, _ = _run("/pull subaru", watches=watches)
    assert service.polled == ["Japaner"]  # brand filter from the URL


def test_no_match_reports_and_polls_nothing():
    service, telegram = _run("/pull porsche")
    assert service.polled == []
    assert "No watch matches 'porsche'" in telegram.sent[-1]


def test_without_argument_polls_everything():
    service, telegram = _run("/pull")
    assert service.polled == [w.name for w in WATCHES]
    assert telegram.sent[0] == "\U0001F504 Polling 5 watches now..."


def test_delete_refuses_ambiguous_partial_name():
    service, telegram = _run("/delete subaru")
    assert service.removed == []
    assert "matches 'Subaru Impreza', 'Subaru Forester'" in telegram.sent[-1]


def test_delete_accepts_unique_partial_name():
    service, telegram = _run("/delete forester")
    assert service.removed == ["Subaru Forester"]


def test_usage_messages_are_valid_telegram_html():
    _, telegram = _run("/delete")
    assert "<number" not in telegram.sent[-1]  # raw "<" makes Telegram reject the message
