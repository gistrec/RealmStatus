"""Collect and format online counts independently of Telegram."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Protocol
from zoneinfo import ZoneInfo

from .history import Online, PlayerRoster

logger = logging.getLogger(__name__)
PLAYER_EMOJI = "👤"


class StatusSource(Protocol):
    async def read(self) -> Online: ...


@dataclass(frozen=True)
class Game:
    title: str
    icon: str
    source: StatusSource
    emoji_key: str = ""
    history_key: str = ""


@dataclass(frozen=True)
class StatusSection:
    title: str
    blocks: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.title}\n" + "\n\n".join(self.blocks) if self.blocks else self.title


@dataclass(frozen=True)
class StatusMessage:
    header: str
    sections: tuple[StatusSection, ...]
    footer: str

    def __str__(self) -> str:
        return "\n\n".join((self.header, *map(str, self.sections), self.footer))


class StatusBoard:
    def __init__(
        self, games: tuple[Game, ...], roster: PlayerRoster | None = None,
        *, poll_interval: int = 60, show_offline_players: bool = True,
    ) -> None:
        self.games = games
        self.roster = roster if roster is not None else PlayerRoster()
        self.poll_interval = poll_interval
        self.show_offline_players = show_offline_players

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
    ) -> StatusMessage:
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
        header = f"{online_icon} <b>Онлайн:</b> {total}"
        sections = []
        if not self.games:
            sections.append(StatusSection("— все игры отключены —"))
        timestamp = now.timestamp()
        for game, result in zip(self.games, results):
            history_key = game.history_key or game.title
            records = self.roster.update(history_key, result, timestamp, self.poll_interval)
            if not self.show_offline_players:
                records = [entry for entry in records if entry[1]]
            count = "—" if result.count is None else str(result.count)
            icon = _icon(icons, game.emoji_key, game.icon)
            title = escape(" ".join(game.title.split())[:128])
            heading = f"{icon} <b>{title}:</b> {count}"
            blocks = []
            if result.note:
                blocks.append(f"— {escape(result.note)} —")
            elif not records and result.count == 0:
                blocks.append("— никого нет —")
            for player, online in records:
                nick = escape(" ".join(player.name.split())[:48])
                head = heads.get((game.emoji_key, player.name.casefold())) if heads else None
                if head is not None:
                    nick = f'<tg-emoji emoji-id="{head}">{PLAYER_EMOJI}</tg-emoji> {nick}'
                if online and player.started_at is not None:
                    activity = f"играет {_duration(timestamp - player.started_at)}"
                elif player.started_at is not None:
                    activity = "текущий статус неизвестен"
                elif player.last_seen is not None:
                    activity = _last_played(timestamp - player.last_seen)
                else:
                    activity = "время последней игры неизвестно"
                blocks.append(f"{nick}\n{activity}")
            if result.count is not None and result.count > len(result.names):
                blocks.append(f"— онлайн без ников: {result.count - len(result.names)} —")
            sections.append(StatusSection(heading, tuple(blocks)))

        self.roster.save()
        updated = now.strftime("%H:%M")
        clock = _icon(icons, "minecraft-clock", "🕒")
        footer = f"{clock} <i>Обновлено: {updated} (МСК)</i>"
        return StatusMessage(header, tuple(sections), footer)


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


def _last_played(seconds: float) -> str:
    minutes = max(0, int(seconds)) // 60
    if minutes == 0:
        return "играл недавно"
    if minutes < 60:
        value, units = minutes, ("минуту", "минуты", "минут")
    elif minutes < 48 * 60:
        value, units = minutes // 60, ("час", "часа", "часов")
    else:
        value, units = minutes // (24 * 60), ("день", "дня", "дней")
    if 11 <= value % 100 <= 14:
        unit = units[2]
    else:
        last_digit = value % 10
        unit = units[0] if last_digit == 1 else units[1] if 2 <= last_digit <= 4 else units[2]
    return f"играл {value} {unit} назад"
