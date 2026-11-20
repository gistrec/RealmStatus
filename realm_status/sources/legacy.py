"""Adapters for the integrations shipped in the original RealmStatus."""

import asyncio

from stardew_valley import StardewValleyService

from ..history import Online, PlayerHistory


class MinecraftRealms:
    def __init__(self, world_id: int) -> None:
        self.world_id = world_id

    def _read(self) -> Online:
        from auth.realms import RealmsService

        names = tuple(RealmsService().online_names(self.world_id))
        return Online(len(names), names)

    async def read(self) -> Online:
        return await asyncio.to_thread(self._read)


class StardewValley:
    def __init__(self, service: StardewValleyService) -> None:
        self.service = service

    async def read(self) -> Online:
        response = await asyncio.to_thread(self.service.get_players)
        players = response["players"]
        names = tuple(player["name"] for player in players if player["isOnline"])
        return Online(len(names), names, players=tuple(PlayerHistory(player["name"]) for player in players))
