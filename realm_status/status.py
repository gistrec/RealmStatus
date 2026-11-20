"""Collect and format online counts independently of Telegram."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Protocol
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
PLAYER_EMOJI = "👤"


@dataclass(frozen=True)
class Online:
    count: int | None
    names: tuple[str, ...] = ()
    note: str = ""
    started_at: Mapping[str, float] = field(default_factory=dict)


class StatusSource(Protocol):
    async def read(self) -> Online: ...


@dataclass(frozen=True)
class Game:
    title: str
    icon: str
    source: StatusSource
    emoji_key: str = ""


class StatusBoard:
    def __init__(self, games: tuple[Game, ...]) -> None:
        self.games = games
        self.joined_at: list[dict[str, float]] = [{} for _ in games]

    async def _read(self, game: Game) -> Online:
        try:
            return await game.source.read()
        except Exception as error:
            logger.warning("%s status failed (%s)", game.title, type(error).__name__)
            return Online(None, note="статус недоступен")

    async def render(
        self,
        icons: Mapping[str, tuple[int, str]] | None = None,
        heads: Mapping[tuple[str, str], int] | None = None,
    ) -> str:
        results = await asyncio.gather(*(self._read(game) for game in self.games))
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        known = [result.count for result in results if result.count is not None]
        online_count = sum(known)
        total = str(online_count) if known or not results else "—"
        if known and len(known) < len(results):
            total += " (по доступным серверам)"

        if online_count > 0:
            online_icon = _icon(icons, "minecraft-heart-full", "❤️")
        elif len(known) == len(results):
            online_icon = _icon(icons, "minecraft-heart-empty", "🤍")
        else:
            online_icon = "👥"
        sections = [f"{online_icon} <b>Онлайн:</b> {total}"]
        if not self.games:
            sections.append("— все игры отключены —")
        for game, result, joined_at in zip(self.games, results, self.joined_at):
            self._observe(result, joined_at, now.timestamp())
            count = "—" if result.count is None else str(result.count)
            icon = _icon(icons, game.emoji_key, game.icon)
            lines = [f"{icon} <b>{escape(game.title)}:</b> {count}"]
            if result.note:
                lines.append(f"— {escape(result.note)} —")
            elif result.count == 0:
                lines.append("— никого нет —")
            else:
                names = result.names[:15]
                for name in names:
                    nick = escape(" ".join(name.split())[:48])
                    head = heads.get((game.emoji_key, name.casefold())) if heads else None
                    if head is not None:
                        nick = f'<tg-emoji emoji-id="{head}">{PLAYER_EMOJI}</tg-emoji> {nick}'
                    if game.emoji_key in {"minecraft", "terraria"}:
                        start = joined_at[name.casefold()]
                        nick += f": играет {_duration(now.timestamp() - start)}"
                    else:
                        nick = f"• {nick}"
                    lines.append(nick)
                if result.count is not None and result.count > len(names):
                    remaining = result.count - len(names)
                    lines.append(f"— без отображения ников: {remaining} —")
            sections.append("\n".join(lines))

        updated = now.strftime("%H:%M")
        clock = _icon(icons, "minecraft-clock", "🕒")
        sections.append(f"{clock} <i>Обновлено: {updated} (МСК)</i>")
        return "\n\n".join(sections)

    @staticmethod
    def _observe(result: Online, joined_at: dict[str, float], now: float) -> None:
        names = {name.casefold() for name in result.names}
        # The Java status sample may omit online players; only a full list proves departure.
        if result.count is not None and len(names) >= result.count:
            for name in joined_at.keys() - names:
                del joined_at[name]
        for name in names:
            if name in result.started_at:
                joined_at[name] = min(now, result.started_at[name])
            else:
                joined_at.setdefault(name, now)


def _icon(icons: Mapping[str, tuple[int, str]] | None, key: str, fallback: str) -> str:
    if icons and key in icons:
        document_id, alt = icons[key]
        return f'<tg-emoji emoji-id="{document_id}">{escape(alt)}</tg-emoji>'
    return escape(fallback)


def _duration(seconds: float) -> str:
    hours, minutes = divmod(max(0, int(seconds)) // 60, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"
