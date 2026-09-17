"""Configuration loading for the notifier: config.yaml (watches) + .env (secrets)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

NotifyKind = Literal["new_listing", "price_drop"]

CONFIG_PATH = Path(os.environ.get("NOTIFIER_CONFIG", "config.yaml"))


class WatchConfig(BaseModel):
    name: str
    query: Optional[str] = None
    location: Optional[str] = None
    radius: Optional[int] = None
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    page_count: int = 1
    interval_minutes: int = 15
    notify_on: List[NotifyKind] = Field(default_factory=lambda: ["new_listing", "price_drop"])
    chat_id: Optional[str] = None  # overrides the global TELEGRAM_CHAT_ID for this watch


class AppConfig(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    watches: List[WatchConfig] = Field(default_factory=list)


def load_config(path: Path = CONFIG_PATH) -> Optional[AppConfig]:
    """Load notifier config, or return None if no config file exists yet.

    Secrets (bot token / default chat id) come from the environment (.env),
    never from config.yaml, so the yaml file is safe to keep in version control.
    """
    load_dotenv()

    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    watches_raw = raw.get("watches", [])
    watches = [WatchConfig(**w) for w in watches_raw]

    return AppConfig(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
        watches=watches,
    )
