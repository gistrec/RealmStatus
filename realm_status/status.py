"""Collect and format online counts independently of Telegram."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Protocol
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Online:
    count: int | None
    names: tuple[str, ...] = ()
    note: str = ""


class StatusSource(Protocol):
    async def read(self) -> Online: ...


@dataclass(frozen=True)
class Game:
    title: str
    icon: str
    source: StatusSource


class StatusBoard:
    def __init__(self, games: tuple[Game, ...]) -> None:
        self.games = games

    async def _read(self, game: Game) -> Online:
        try:
            return await game.source.read()
        except Exception as error:
            logger.warning("%s status failed (%s)", game.title, type(error).__name__)
            return Online(None, note="статус недоступен")

    async def render(self) -> str:
        results = await asyncio.gather(*(self._read(game) for game in self.games))
        known = [result.count for result in results if result.count is not None]
        total = str(sum(known)) if known or not results else "—"
        if known and len(known) < len(results):
            total += " (по доступным серверам)"

        sections = [f"👥 <b>Онлайн:</b> {total}"]
        if not self.games:
            sections.append("— все игры отключены —")
        for game, result in zip(self.games, results):
            count = "—" if result.count is None else str(result.count)
            lines = [f"{escape(game.icon)} <b>{escape(game.title)}:</b> {count}"]
            if result.note:
                lines.append(f"— {escape(result.note)} —")
            elif result.count == 0:
                lines.append("— никого нет —")
            else:
                names = result.names[:15]
                lines.extend(
                    f"• {escape(' '.join(name.split())[:48])}" for name in names
                )
                if result.count is not None and result.count > len(names):
                    remaining = result.count - len(names)
                    lines.append(f"— без отображения ников: {remaining} —")
            sections.append("\n".join(lines))

        updated = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%H:%M")
        sections.append(f"🕒 <i>Обновлено: {updated} (МСК)</i>")
        return "\n\n".join(sections)
