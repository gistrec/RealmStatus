"""Read existing player totals and join/leave events from a local Java server."""

import gzip
import hashlib
import json
import logging
import math
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID

from ..history import PlayerHistory

logger = logging.getLogger(__name__)
PLAYER_EVENT = re.compile(
    r"^\[(\d\d:\d\d:\d\d)\] \[Server thread/INFO\]: "
    r"(?:System chat: )?([\w.]{1,32}) (joined|left) the game$"
)
PLAYER_ID = re.compile(
    r"UUID of player ([\w.]{1,32}) is ([0-9a-fA-F-]{36})"
)


class MinecraftHistory:
    def __init__(self, directory: Path, world: Path | None = None) -> None:
        self.directory = directory
        self.world = world or directory / self._world_name()
        self.logs: dict[Path, tuple[int, dict[str, str], dict[str, tuple[float, str, str]]]] = {}

    def _world_name(self) -> str:
        try:
            for line in (self.directory / "server.properties").read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "level-name" and value.strip():
                    return value.strip()
        except OSError:
            pass
        return "world"

    def _log(self, path: Path) -> tuple[dict[str, str], dict[str, tuple[float, str, str]]]:
        stamp = path.stat()
        cached = self.logs.get(path)
        if cached and cached[0] == stamp.st_mtime_ns:
            return cached[1], cached[2]
        names, events = {}, {}
        last_write = datetime.fromtimestamp(stamp.st_mtime)
        day = date.fromisoformat(path.name[:10]) if path.suffix == ".gz" else last_write.date()
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as lines:
            for line in lines:
                identity = PLAYER_ID.search(line)
                if identity:
                    names[identity[2].lower()] = identity[1]
                match = PLAYER_EVENT.fullmatch(line.strip())
                if not match:
                    continue
                clock, name, action = match.groups()
                moment = datetime.combine(day, time.fromisoformat(clock))
                if path.suffix != ".gz" and moment > last_write + timedelta(minutes=2):
                    moment -= timedelta(days=1)
                events[name.casefold()] = (moment.timestamp(), action, name)
        self.logs[path] = (stamp.st_mtime_ns, names, events)
        return names, events

    def read(self) -> tuple[tuple[PlayerHistory, ...], dict[str, float]]:
        names: dict[str, str] = {}
        roster: dict[str, PlayerHistory] = {}
        events: dict[str, tuple[float, str, str]] = {}
        log_dir = self.directory / "logs"
        for path in sorted([*log_dir.glob("????-??-??-*.log.gz"), log_dir / "latest.log"]):
            try:
                identities, log_events = self._log(path)
            except (OSError, EOFError, ValueError):
                continue
            names.update(identities)
            for key, event in log_events.items():
                if key not in events or event[0] >= events[key][0]:
                    events[key] = event

        for key, (timestamp, action, name) in events.items():
            offline_uuid = UUID(bytes=hashlib.md5(f"OfflinePlayer:{name}".encode()).digest(), version=3)
            names.setdefault(str(offline_uuid), name)
            roster[key] = PlayerHistory(name, last_seen=timestamp)
        # Access lists can resolve older UUIDs after they disappear from the user cache.
        for filename in ("banned-players.json", "whitelist.json", "ops.json", "usercache.json"):
            try:
                cached_players = json.loads((self.directory / filename).read_bytes())
                names.update((str(row["uuid"]).lower(), row["name"]) for row in cached_players)
            except (OSError, ValueError, TypeError, KeyError) as error:
                logger.debug("Имена Minecraft из %s недоступны (%s).", filename, type(error).__name__)

        for folder in (self.world / "stats", self.world / "players/stats"):
            for path in folder.glob("*.json"):
                name = names.get(path.stem.lower())
                if not name:
                    continue
                try:
                    custom = json.loads(path.read_bytes())["stats"]["minecraft:custom"]
                    ticks = float(custom.get("minecraft:play_time", custom.get("minecraft:play_one_minute", 0)))
                    if not math.isfinite(ticks) or ticks <= 0:
                        continue
                    key = name.casefold()
                    previous = roster.get(key)
                    event = events.get(key)
                    if event is not None and event[1] == "left":
                        timestamp = event[0]
                    else:
                        timestamp = max(path.stat().st_mtime, (previous.last_seen or 0) if previous else 0)
                    roster[key] = PlayerHistory(
                        name, max(ticks / 20, previous.seconds or 0) if previous else ticks / 20, timestamp
                    )
                except (OSError, ValueError, TypeError, KeyError):
                    continue
        return tuple(roster.values()), {
            key: timestamp for key, (timestamp, action, _) in events.items() if action == "joined"
        }
