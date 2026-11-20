"""Wire configuration, status sources and local server controls."""

import argparse
import asyncio
import logging
from collections.abc import Callable

from telethon import TelegramClient, errors

from .control import TunnelControl
from .emoji.images import EmojiImages
from .emoji.pack import EmojiPack
from .emoji.settings import EmojiSettings
from .games import GameCatalog
from .history import PlayerRoster
from .lifecycle import STATE, BotRuntime, bot_lock
from .settings import PROJECT_DIR, LocalConfig, TelegramSettings, load_environment, required
from .telegram import TelegramBot


async def run(preview: bool, on_ready: Callable[[], None] | None = None) -> None:
    local_config = LocalConfig.load()
    config = TelegramSettings.load(local_config)
    emoji_settings = EmojiSettings.load(local_config)
    roster = PlayerRoster(STATE / "player-roster.json", writable=not preview)
    board, controls = GameCatalog.load(local_config).build(
        roster, poll_interval=config.interval, show_offline_players=config.show_offline_players
    )
    tunnel = TunnelControl.load(local_config)
    if preview:
        print(await board.render())
        return

    api_id = int(required("TELEGRAM_API_ID"))
    if api_id <= 0:
        raise ValueError("TELEGRAM_API_ID должен быть положительным числом.")
    api_hash = required("TELEGRAM_API_HASH")
    token = required("TELEGRAM_BOT_TOKEN")
    session_directory = PROJECT_DIR / ".state"
    session_directory.mkdir(mode=0o700, exist_ok=True)
    client = TelegramClient(
        str(session_directory / "telegram"), api_id, api_hash,
        flood_sleep_threshold=0,
    )
    emoji_pack = None
    if emoji_settings.enabled:
        emoji_pack = EmojiPack(
            emoji_settings, PROJECT_DIR / "assets/emoji/generated", token, client
        )
    try:
        await TelegramBot(
            client, config, board, controls, tunnel=tunnel, emoji_pack=emoji_pack
        ).run(token, on_ready=on_ready)
    finally:
        await client.disconnect()


def login_to_realms(game_id: str | None) -> None:
    world_id = GameCatalog.load(LocalConfig.load()).realms_profile(game_id).realm_id()
    from auth.realms import RealmsService

    realms = RealmsService()
    client, token, profile = realms.authenticate(interactive=True)
    if world_id not in realms.world_ids(client, token, profile):
        raise ValueError(f"Мира Realms {world_id} нет в списке миров этого аккаунта.")
    print(f"Вход выполнен. Realms {world_id} доступен для {profile['name']}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Print the status without Telegram")
    mode.add_argument(
        "--emoji-prepare", action="store_true", help="Prepare PNG images without Telegram"
    )
    mode.add_argument(
        "--realms-login", nargs="?", const="", metavar="GAME",
        help="Authorize the account for a configured Realms source",
    )
    args = parser.parse_args()
    load_environment()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.emoji_prepare:
            images = EmojiImages(
                EmojiSettings.load(LocalConfig.load()), PROJECT_DIR / "assets/emoji/generated"
            ).prepare()
            for image in images:
                print(image.path)
            print(f"Подготовлено PNG для Telegram: {len(images)}.")
        elif args.realms_login is not None:
            login_to_realms(args.realms_login or None)
        elif args.preview:
            asyncio.run(run(preview=True))
        else:
            with bot_lock(STATE):
                runtime = BotRuntime()
                asyncio.run(runtime.run(lambda: run(preview=False, on_ready=runtime.mark_ready)))
    except (ValueError, RuntimeError, OSError, errors.RPCError) as error:
        details = error if isinstance(error, (ValueError, RuntimeError)) else ""
        logging.error("Ошибка запуска (%s). %s", type(error).__name__, details)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
