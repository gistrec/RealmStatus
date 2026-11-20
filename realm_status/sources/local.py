"""Read the Java status protocol and Terraria viewer telemetry."""

import asyncio
import json
import logging
import time
from urllib.parse import urljoin
from urllib.request import urlopen

from mcstatus import JavaServer

from ..status import Online

logger = logging.getLogger(__name__)


class MinecraftServer:
    def __init__(self, host: str, port: int) -> None:
        self.server = JavaServer(host, port, timeout=4)

    async def read(self) -> Online:
        try:
            result = await self.server.async_status(tries=1)
        except ConnectionRefusedError:
            return Online(0, note="сервер остановлен")
        names = tuple(sorted(player.name for player in result.players.sample or ()))
        return Online(result.players.online, names)


class TerrariaServer:
    def __init__(self, url: str, port: int, *, host: str = "127.0.0.1") -> None:
        self.url = url
        self.port = port
        self.host = host

    def _read_telemetry(self) -> Online:
        with urlopen(self.url, timeout=4) as response:
            payload = json.load(response)
        server = payload["server"]
        checked_at = server["players_checked_at"]
        if not (
            payload["server_info"]["telemetry_connected"]
            and server["listening"]
            and checked_at is not None
            and time.time() - checked_at < 30
        ):
            return Online(None, note="ожидаю телеметрию")
        names = tuple(sorted(server["player_names"]))
        started_at = self._session_starts(names) if names else {}
        return Online(server["players_online"], names, started_at=started_at)

    def _session_starts(self, names: tuple[str, ...]) -> dict[str, float]:
        url = urljoin(self.url, "/api/section-data?section=players")
        try:
            with urlopen(url, timeout=4) as response:
                detail = json.load(response)
            online_ids = set(detail["online"]["ids"])
            observed = {name.casefold() for name in names}
            starts = {}
            for player in detail["players"]:
                name = player["name"].casefold()
                if player["id"] not in online_ids or name not in observed:
                    continue
                sessions = player["sessions"]
                active = sessions[-1] if sessions else None
                if active and active.get("ended_at") is None:
                    start = float(active["started_at"])
                    if 0 < start <= time.time():
                        starts[name] = start
            return starts
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.debug("Сессии Terraria недоступны (%s).", type(error).__name__)
            return {}

    async def read(self) -> Online:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=4
            )
        except ConnectionRefusedError:
            return Online(0, note="сервер остановлен")

        writer.close()
        await writer.wait_closed()
        return await asyncio.to_thread(self._read_telemetry)
