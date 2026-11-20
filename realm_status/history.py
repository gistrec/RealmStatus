"""Merge game history with the sessions observed by the bot."""

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlayerHistory:
    name: str
    seconds: float | None = None
    last_seen: float | None = None


@dataclass(frozen=True)
class Online:
    count: int | None
    names: tuple[str, ...] = ()
    note: str = ""
    started_at: Mapping[str, float] = field(default_factory=dict)
    players: tuple[PlayerHistory, ...] = ()


@dataclass
class PlayerRecord:
    name: str
    seconds: float = 0
    last_seen: float | None = None
    started_at: float | None = None


class PlayerRoster:
    def __init__(self, path: Path | None = None, *, writable: bool = False) -> None:
        self.path = path
        self.writable = writable
        self.records: dict[str, dict[str, PlayerRecord]] = {}
        self.previous_poll: dict[str, float] = {}
        if path is not None and path.is_file():
            try:
                state = json.loads(path.read_bytes())
                self.records = {
                    game: {key: PlayerRecord(**record) for key, record in players.items()}
                    for game, players in state.items()
                }
            except (OSError, ValueError, TypeError, AttributeError) as error:
                logger.warning("История RealmStatus не прочитана (%s).", type(error).__name__)

    def update(
        self, game: str, status: Online, now: float, poll_interval: int
    ) -> list[tuple[PlayerRecord, bool]]:
        players = self.records.setdefault(game, {})
        observed = {name.casefold(): name for name in status.names if name.strip()}
        full = status.count is not None and len(observed) >= status.count
        previous_poll = self.previous_poll.get(game)
        continuous = previous_poll is not None and 0 <= now - previous_poll <= poll_interval * 2 + 5
        earlier_seen = {key: player.last_seen for key, player in players.items()}

        if status.count is not None:
            for key, name in observed.items():
                player = players.setdefault(key, PlayerRecord(name))
                player.name = name
                if continuous and player.started_at is not None and player.last_seen == previous_poll:
                    player.seconds += now - previous_poll
                if player.started_at is None or not continuous:
                    player.started_at = now
                if key in status.started_at:
                    player.started_at = min(now, status.started_at[key])
                player.last_seen = now
            self.previous_poll[game] = now
        else:
            self.previous_poll.pop(game, None)

        for row in status.players:
            if not row.name.strip():
                continue
            key = row.name.casefold()
            player = players.setdefault(key, PlayerRecord(row.name))
            if key not in observed:
                player.name = row.name
            if row.last_seen is not None:
                player.last_seen = max(player.last_seen or 0, min(now, row.last_seen))
            if row.seconds is not None:
                player.seconds = max(player.seconds, row.seconds)

        if full:
            for key, player in players.items():
                if key not in observed and player.started_at is not None:
                    # Keep the source's newer logout timestamp when available.
                    if continuous and earlier_seen[key] == previous_poll and player.last_seen == previous_poll:
                        player.last_seen = now
                    player.started_at = None

        return sorted(
            ((player, key in observed) for key, player in players.items()),
            key=lambda entry: (not entry[1], -entry[0].seconds, entry[0].name.casefold()),
        )

    def save(self) -> None:
        if not self.writable or self.path is None:
            return
        try:
            state = {
                game: {key: asdict(record) for key, record in players.items()}
                for game, players in self.records.items()
            }
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as file:
                json.dump(state, file, ensure_ascii=False)
            Path(file.name).replace(self.path)
        except OSError as error:
            logger.warning("История RealmStatus не сохранена (%s).", type(error).__name__)
