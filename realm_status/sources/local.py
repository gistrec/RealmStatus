"""Read the Java status protocol and Terraria viewer telemetry."""

import asyncio
import json
import logging
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen

from mcstatus import JavaServer

from ..history import Online, PlayerHistory
from .minecraft_history import MinecraftHistory

logger = logging.getLogger(__name__)


class MinecraftServer:
    def __init__(self, host: str, port: int, history: MinecraftHistory | None = None) -> None:
        self.server = JavaServer(host, port, timeout=4)
        self.history = history

    async def read(self) -> Online:
        players, starts = await asyncio.to_thread(self.history.read) if self.history else ((), {})
        try:
            result = await self.server.async_status(tries=1)
        except ConnectionRefusedError:
            return Online(0, note="сервер остановлен", players=players)
        except (OSError, ValueError):
            return Online(None, note="статус недоступен", players=players)
        names = tuple(sorted(player.name for player in result.players.sample or ()))
        return Online(result.players.online, names, started_at=starts, players=players)


class TerrariaServer:
    def __init__(
        self, url: str, port: int, *, host: str = "127.0.0.1",
        history_dir: Path | None = None,
    ) -> None:
        self.url = url
        self.port = port
        self.host = host
        self.history_dir = history_dir

    def _read_telemetry(self) -> Online:
        players, starts = self._history()
        try:
            with urlopen(self.url, timeout=4) as response:
                payload = json.load(response)
            server = payload["server"]
        except (OSError, ValueError, TypeError, KeyError):
            return Online(None, note="статус недоступен", players=players)
        checked_at = server["players_checked_at"]
        if not (
            payload["server_info"]["telemetry_connected"]
            and server["listening"]
            and checked_at is not None
            and time.time() - checked_at < 30
        ):
            return Online(None, note="ожидаю телеметрию", players=players)
        names = tuple(sorted(server["player_names"]))
        return Online(server["players_online"], names, started_at=starts, players=players)

    def _history(self, *, stopped: bool = False) -> tuple[tuple[PlayerHistory, ...], dict[str, float]]:
        url = urljoin(self.url, "/api/section-data?section=players")
        try:
            if stopped and self.history_dir is not None:
                return self._cached_history()
            with urlopen(url, timeout=4) as response:
                detail = json.load(response)
            online_ids = set(detail["online"]["ids"]) if not stopped else set()
            return self._read_players(detail["players"], online_ids)
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.debug("Сессии Terraria недоступны (%s).", type(error).__name__)
            return self._cached_history()

    def _cached_history(self) -> tuple[tuple[PlayerHistory, ...], dict[str, float]]:
        if self.history_dir is not None:
            try:
                data = json.loads((self.history_dir / "player-history.json").read_bytes())
                return self._read_players(data["players"].values(), set())
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
        return (), {}

    @staticmethod
    def _read_players(rows, online_ids: set[str]) -> tuple[tuple[PlayerHistory, ...], dict[str, float]]:
        players = []
        starts = {}
        now = time.time()
        for player in rows:
            try:
                name = player["name"].strip()
                if not name:
                    continue
                sessions = player.get("sessions", [])
                last = sessions[-1] if sessions else {}
                last_seen = last.get("ended_at") or player.get("last_seen_at")
                seconds = float(player["total_seconds"]) if player.get("total_seconds") is not None else None
                timestamp = float(last_seen) if last_seen is not None else None
                if player["id"] in online_ids and last and last.get("ended_at") is None:
                    start = float(last["started_at"])
                    if 0 < start <= now:
                        starts[name.casefold()] = start
                players.append(PlayerHistory(name, seconds, timestamp))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return tuple(players), starts

    async def read(self) -> Online:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=4
            )
        except ConnectionRefusedError:
            players, _ = await asyncio.to_thread(self._history, stopped=True)
            return Online(0, note="сервер остановлен", players=players)
        except (OSError, TimeoutError):
            players, _ = await asyncio.to_thread(self._history)
            return Online(None, note="статус недоступен", players=players)

        writer.close()
        await writer.wait_closed()
        return await asyncio.to_thread(self._read_telemetry)
