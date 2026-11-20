"""Authenticate with Realms and fetch world presence using the existing token store."""

from typing import TypedDict, cast

from requests import HTTPError

from .microsoft import MicrosoftAuth
from .minecraft import MinecraftAuth
from .xbox import XboxAuth


class MinecraftProfile(TypedDict):
    id: str
    name: str


class RealmPlayer(TypedDict):
    name: str
    online: bool


class RealmWorld(TypedDict):
    id: int


class RealmWorldsResponse(TypedDict):
    servers: list[RealmWorld]


class RealmWorldInfoResponse(TypedDict):
    players: list[RealmPlayer]


def _is_auth_error(error: HTTPError) -> bool:
    response = getattr(error, "response", None)
    if response is None:
        return False
    return response.status_code in {401, 403}


class RealmsService:
    def authenticate(self, *, interactive: bool = False) -> tuple[MinecraftAuth, str, MinecraftProfile]:
        ms = MicrosoftAuth()
        xbox = XboxAuth()
        mc = MinecraftAuth()

        # Microsoft
        try:
            token = ms.get_access_token()
        except Exception:
            if not interactive:
                raise
            token = ms.login()

        # Xbox
        xbl_token, uhs = xbox.get_xbl_token(token)
        try:
            xsts_token, uhs = xbox.get_xsts_token(xbl_token)
        except HTTPError as error:
            if not _is_auth_error(error):
                raise
            xbl_token, uhs = xbox.authenticate(token)
            xsts_token, uhs = xbox.authorize_xsts(xbl_token)

        # Minecraft
        try:
            mc_token = mc.get_token(xsts_token, uhs)
        except HTTPError as error:
            if not _is_auth_error(error):
                raise
            xbl_token, uhs = xbox.authenticate(token)
            xsts_token, uhs = xbox.authorize_xsts(xbl_token)
            mc_token = mc.authenticate(xsts_token, uhs)

        try:
            profile = cast(MinecraftProfile, mc.get_profile(mc_token))
        except HTTPError as error:
            if not _is_auth_error(error):
                raise
            mc_token = mc.authenticate(xsts_token, uhs)
            profile = cast(MinecraftProfile, mc.get_profile(mc_token))
        return mc, mc_token, profile

    def world_ids(self, mc: MinecraftAuth, token: str, profile: MinecraftProfile) -> list[int]:
        response = mc.get_worlds(token, profile["id"], profile["name"])
        worlds = cast(RealmWorldsResponse, response)
        return [world["id"] for world in worlds["servers"]]

    def online_names(self, world_id: int) -> list[str]:
        mc, token, profile = self.authenticate()
        return self.world_online_names(mc, token, profile, world_id)

    def world_online_names(
        self, mc: MinecraftAuth, token: str, profile: MinecraftProfile, world_id: int
    ) -> list[str]:
        response = mc.get_world_info(token, profile["id"], profile["name"], world_id)
        world = cast(RealmWorldInfoResponse, response)
        return sorted(player["name"] for player in world["players"] if player["online"])
