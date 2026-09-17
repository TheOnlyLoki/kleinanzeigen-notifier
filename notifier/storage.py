"""Tiny JSON-file-backed store of ads already seen per watch.

One file per watch under data/ so state survives container restarts when
data/ is a mounted volume. No database needed at this scale.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

DATA_DIR = Path("data")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "watch"


class SeenAdsStore:
    def __init__(self, watch_name: str, data_dir: Path = DATA_DIR):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._path = data_dir / f"{_slug(watch_name)}.json"
        self._lock = asyncio.Lock()
        self._records: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, adid: str) -> Optional[Dict[str, Any]]:
        return self._records.get(adid)

    def upsert(self, adid: str, **fields: Any) -> None:
        self._records.setdefault(adid, {}).update(fields)

    async def save(self) -> None:
        async with self._lock:
            tmp_path = self._path.with_suffix(".tmp")
            await asyncio.to_thread(
                tmp_path.write_text, json.dumps(self._records, ensure_ascii=False, indent=2)
            )
            await asyncio.to_thread(tmp_path.replace, self._path)
