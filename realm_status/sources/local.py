"""Read the Java status protocol and Terraria viewer telemetry."""

import asyncio
import json
import time
from urllib.request import urlopen

from mcstatus import JavaServer

from ..status import Online


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
        return Online(server["players_online"], tuple(sorted(server["player_names"])))

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
