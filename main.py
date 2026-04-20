import asyncio
from typing import TypedDict, cast

from requests import HTTPError, RequestException

from auth.microsoft import MicrosoftAuth
from auth.xbox import XboxAuth
from auth.minecraft import MinecraftAuth

from tg import update_status
from db import set_setting
from stardew_valley import StardewValleyService


LAST_BACKUP_URL = "last_backup_url"


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


class RealmBackupResponse(TypedDict):
    downloadLink: str


def _load_stardew_online_players() -> list[str]:
    try:
        service = StardewValleyService()
        return sorted(service.get_online_player_names())
    except (RuntimeError, RequestException) as error:
        print(f"Failed to load Stardew Valley status: {error}")
        return []


def _is_auth_error(error: HTTPError) -> bool:
    response = getattr(error, "response", None)
    if response is None:
        return False
    return response.status_code in {401, 403}


async def main() -> None:
    ms = MicrosoftAuth()
    xbox = XboxAuth()
    mc = MinecraftAuth()

    # Microsoft
    try:
        token = ms.get_access_token()
    except Exception:
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

    uuid = profile["id"]
    name = profile["name"]

    print(f"Logged in as: {name}")

    minecraft_online_players: list[str] = []
    worlds = cast(RealmWorldsResponse, mc.get_worlds(mc_token, uuid, name))
    for world in worlds["servers"]:
        if world["id"] != 12829680:
            continue

        print("=== world info ===")
        world_info = cast(RealmWorldInfoResponse, mc.get_world_info(mc_token, uuid, name, world["id"]))
        minecraft_online_players = sorted([player["name"] for player in world_info["players"] if player["online"]])
        print(minecraft_online_players)

        print("\n=== backup ===")
        last_backup = cast(RealmBackupResponse, mc.get_world_last_backup(mc_token, uuid, name, world["id"], 1))
        set_setting(LAST_BACKUP_URL, last_backup["downloadLink"])

    stardew_online_players = _load_stardew_online_players()
    await update_status(minecraft_online_players, stardew_online_players)


if __name__ == "__main__":
    asyncio.run(main())
