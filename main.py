import asyncio
from typing import TypedDict, cast

from requests import RequestException

from auth.realms import RealmsService

from tg import update_status
from db import set_setting
from stardew_valley import StardewValleyService


LAST_BACKUP_URL = "last_backup_url"


class RealmBackupResponse(TypedDict):
    downloadLink: str


def _load_stardew_online_players() -> list[str]:
    try:
        service = StardewValleyService()
        return sorted(service.get_online_player_names())
    except (RuntimeError, RequestException) as error:
        print(f"Failed to load Stardew Valley status: {error}")
        return []


async def main() -> None:
    realms = RealmsService()
    mc, mc_token, profile = realms.authenticate(interactive=True)

    uuid = profile["id"]
    name = profile["name"]

    print(f"Logged in as: {name}")

    minecraft_online_players: list[str] = []
    for world_id in realms.world_ids(mc, mc_token, profile):
        if world_id != 12829680:
            continue

        print("=== world info ===")
        minecraft_online_players = realms.world_online_names(mc, mc_token, profile, world_id)
        print(minecraft_online_players)

        print("\n=== backup ===")
        last_backup = cast(RealmBackupResponse, mc.get_world_last_backup(mc_token, uuid, name, world_id, 1))
        set_setting(LAST_BACKUP_URL, last_backup["downloadLink"])

    stardew_online_players = _load_stardew_online_players()
    await update_status(minecraft_online_players, stardew_online_players)


def handler(event, context):
    asyncio.run(main())
    return {
        'statusCode': 200
    }


if __name__ == "__main__":
    handler(None, None)
