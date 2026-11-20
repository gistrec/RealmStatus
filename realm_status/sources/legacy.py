"""Adapters for the integrations shipped in the original RealmStatus."""

import asyncio

from stardew_valley import StardewValleyService

from ..status import Online


class MinecraftRealms:
    def __init__(self, world_id: int) -> None:
        self.world_id = world_id

    def _read(self) -> Online:
        from auth.realms import RealmsService

        names = RealmsService().online_names(self.world_id)
        return Online(len(names), tuple(names))

    async def read(self) -> Online:
        return await asyncio.to_thread(self._read)


class StardewValley:
    def __init__(self, service: StardewValleyService) -> None:
        self.service = service

    async def read(self) -> Online:
        names = await asyncio.to_thread(self.service.get_online_player_names)
        return Online(len(names), tuple(sorted(names)))
