"""Collect and format online counts independently of Telegram."""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from html import escape
from pathlib import Path
from textwrap import wrap
from typing import Protocol
from zoneinfo import ZoneInfo

from PIL import ImageFont

from .history import Online, PlayerRoster

logger = logging.getLogger(__name__)
PLAYER_EMOJI = "👤"
HEADING_FONT_SIZE = 17
STATUS_LINE_LIMIT = 25
NONBREAKING_SPACE = "\N{NO-BREAK SPACE}"
NARROW_NONBREAKING_SPACE = "\N{NARROW NO-BREAK SPACE}"
STATUS_FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"


class StatusSource(Protocol):
    async def read(self) -> Online: ...


@dataclass(frozen=True)
class PublicAddress:
    host: str
    port: int
    bedrock_port: int | None = None


@dataclass(frozen=True)
class Game:
    title: str
    icon: str
    source: StatusSource
    emoji_key: str = ""
    history_key: str = ""
    public_address: PublicAddress | None = None


@dataclass(frozen=True)
class StatusSection:
    title: str
    blocks: tuple[str, ...] = ()

    def __str__(self) -> str:
        return "\n\n".join((self.title, *self.blocks))


@dataclass(frozen=True)
class StatusMessage:
    header: str
    sections: tuple[StatusSection, ...]
    footer: str

    def __str__(self) -> str:
        return self.render_sections(self.sections)

    def render_sections(self, sections: Iterable[StatusSection]) -> str:
        return "\n\n".join((self.header, *map(str, sections), self.footer))


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
            total += "\n(доступные серверы)"

        if online_count > 0:
            online_icon = _icon(icons, "minecraft-heart-full", "❤️")
        elif len(known) == len(results):
            online_icon = _icon(icons, "minecraft-heart-empty", "🤍")
        else:
            online_icon = "👥"
        header = f"{online_icon} <b>Онлайн:</b> {total}"
        sections = []
        if not self.games:
            header += "\n\n— все игры отключены —"
        timestamp = now.timestamp()
        for game, result in zip(self.games, results):
            history_key = game.history_key or game.title
            records = self.roster.update(history_key, result, timestamp, self.poll_interval)
            if not self.show_offline_players:
                records = [entry for entry in records if entry[1]]
            icon = _icon(icons, game.emoji_key, game.icon)
            if result.note:
                server_status = result.note
            elif result.count is None:
                server_status = "статус недоступен"
            else:
                players = _plural(result.count, ("игрок", "игрока", "игроков"))
                server_status = f"онлайн {result.count} {players}"
            heading = _game_heading(game, icon, server_status)
            blocks = []
            for player, online in records:
                head = heads.get((game.emoji_key, player.name.casefold())) if heads else None
                name = " ".join(player.name.split())[:48]
                nick_limit = STATUS_LINE_LIMIT - (2 if head is not None else 0)
                nick_lines = wrap(name, nick_limit, break_on_hyphens=False)
                nick = "\n".join(f"<code>{escape(line)}</code>" for line in nick_lines)
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
                activity_lines = "\n".join(wrap(activity, STATUS_LINE_LIMIT))
                blocks.append(f"{nick}\n{activity_lines}")
            if result.count is not None and result.count > len(result.names):
                missing = f"— онлайн без ников: {result.count - len(result.names)} —"
                blocks.append("\n".join(wrap(missing, STATUS_LINE_LIMIT)))
            sections.append(StatusSection(heading, tuple(blocks)))

        self.roster.save()
        updated = now.strftime("%H:%M")
        clock = _icon(icons, "minecraft-clock", "🕒")
        footer = f"{clock} <i>Обновлено: {updated} (МСК)</i>"
        return StatusMessage(header, tuple(sections), footer)


@lru_cache(maxsize=2)
def _heading_font(bold: bool = False) -> ImageFont.FreeTypeFont:
    filename = "OpenSans-SemiBold.ttf" if bold else "OpenSans-Regular.ttf"
    return ImageFont.truetype(STATUS_FONTS / filename, HEADING_FONT_SIZE)


def _game_heading(game: Game, icon: str, status: str) -> str:
    font = _heading_font()
    title = " ".join(game.title.split())[:128]
    icon_length = 1 if icon.startswith("<tg-emoji ") else len(game.icon)
    first_title, *rest = wrap(title, max(1, STATUS_LINE_LIMIT - icon_length - 5))
    info = [(line, f"<b>{escape(line)}</b>", _heading_font(True).getlength(line)) for line in rest]
    address = game.public_address
    if address is not None:
        for line in wrap(address.host, STATUS_LINE_LIMIT, break_on_hyphens=False):
            info.append((line, escape(line), font.getlength(line)))
        ports = []
        if game.emoji_key == "minecraft":
            if address.bedrock_port is not None:
                ports.append(("порт Bedrock: ", address.bedrock_port))
            ports.append(("порт Java: ", address.port))
        else:
            ports.append(("порт: ", address.port))
        for label, port in ports:
            number = str(port)
            width = font.getlength(label) + len(number) * HEADING_FONT_SIZE * 0.6
            info.append((f"{label}{number}", f"{label}<code>{number}</code>", width))
    for line in wrap(status, STATUS_LINE_LIMIT):
        info.append((line, escape(line), font.getlength(line)))

    line_unit = font.getlength("_")
    gap = font.getlength(NONBREAKING_SPACE)
    narrow_gap = font.getlength(NARROW_NONBREAKING_SPACE)
    # Telegram renders the game icon as an inline emoji before the bold title.
    title_width = _heading_font(True).getlength(first_title) + 3 * gap + HEADING_FONT_SIZE * 1.5
    frame_width = STATUS_LINE_LIMIT * line_unit
    side_limit = (STATUS_LINE_LIMIT - len(first_title) - icon_length - 3) // 2
    side_length = max(0, min(side_limit, round((frame_width - title_width) / (2 * line_unit))))
    sides = "_" * side_length
    heading = f"{icon} <b>{escape(first_title)}</b>"
    lines = [f"{sides} {heading} {sides}" if sides else heading]
    for plain, markup, width in info:
        offset = max(0, (frame_width - width) / 2)
        room = STATUS_LINE_LIMIT - len(plain)
        spaces = min(room, int(offset / gap))
        narrow = min(room - spaces, round((offset - spaces * gap) / narrow_gap))
        indent = NONBREAKING_SPACE * spaces + NARROW_NONBREAKING_SPACE * narrow
        lines.append(f"{indent}{markup}")
    lines.append("_" * STATUS_LINE_LIMIT)
    return "\n".join(lines)


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
    return f"играл {value} {_plural(value, units)} назад"


def _plural(value: int, units: tuple[str, str, str]) -> str:
    if 11 <= value % 100 <= 14:
        return units[2]
    last_digit = value % 10
    if last_digit == 1:
        return units[0]
    return units[1] if 2 <= last_digit <= 4 else units[2]
