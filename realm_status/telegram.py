"""Resolve the target peer, edit its status message and handle admin commands."""

import asyncio
import logging
import re
from collections.abc import Callable

from telethon import Button, TelegramClient, errors, events, utils
from telethon.extensions import html
from telethon.tl.types import MessageEntityCustomEmoji, ReplyInlineMarkup

from .control import ServerControl, TunnelControl
from .emoji.pack import EmojiPack, EmojiPackError
from .games import GAME_ID
from .pagination import status_pages
from .settings import TelegramSettings
from .status import PLAYER_EMOJI, StatusBoard

logger = logging.getLogger(__name__)
COMMAND = re.compile(
    rf"/(status|emoji_sync|tunnel_restart|{GAME_ID.pattern}_(?:start|stop))"
    r"(?:@([A-Za-z0-9_]+))?"
)
PLAYER_HEAD_TAG = re.compile(rf'<tg-emoji emoji-id="\d+">{PLAYER_EMOJI}</tg-emoji> ')


class TelegramBot:
    def __init__(
        self,
        client: TelegramClient,
        config: TelegramSettings,
        board: StatusBoard,
        controls: dict[str, ServerControl],
        tunnel: TunnelControl | None = None,
        emoji_pack: EmojiPack | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.board = board
        self.controls = controls
        self.tunnel = tunnel
        self.emoji_pack = emoji_pack
        self.custom_emoji_rejected = False
        self.emoji_snapshot = None
        self.refresh = asyncio.Event()
        self.target_peer = None
        self.username = ""
        self.pages: tuple[str, ...] = ()
        self.page = 0
        self.page_lock = asyncio.Lock()

    async def _target(self):
        if self.target_peer is None:
            reference = self.config.chat_username or self.config.chat_id
            peer = await self.client.get_input_entity(reference)
            if utils.get_peer_id(peer) != self.config.chat_id:
                raise ValueError("telegram.chat_username points to a different chat")
            self.target_peer = peer
        return self.target_peer

    async def _publish(self) -> None:
        while True:
            self.refresh.clear()
            delay = self.config.interval
            try:
                pack = self.emoji_pack
                snapshot = (pack.icons, pack.heads) if pack else None
                if snapshot != self.emoji_snapshot:
                    self.custom_emoji_rejected = False
                    self.emoji_snapshot = snapshot
                if self.custom_emoji_rejected:
                    pack = None
                message = await self.board.render(
                    pack.icons if pack else None, pack.heads if pack else None
                )
                pages = status_pages(message)
                async with self.page_lock:
                    self.pages = pages
                    self.page = min(self.page, len(pages) - 1)
                    await self._show_page(await self._target())
            except errors.MessageNotModifiedError:
                pass
            except errors.FloodWaitError as error:
                await asyncio.sleep(error.seconds + 1)
            except (
                errors.MessageAuthorRequiredError,
                errors.MessageIdInvalidError,
                errors.MessageEditTimeExpiredError,
                errors.ChatAdminRequiredError,
                errors.ChatWriteForbiddenError,
            ) as error:
                logger.error(
                    "Cannot edit configured message %s in chat %s (%s)",
                    self.config.message_id, self.config.chat_id, type(error).__name__,
                )
            except ValueError:
                logger.warning(
                    "Cannot resolve chat %s. Check its username or mention the bot in that chat.",
                    self.config.chat_id,
                )
            except (OSError, errors.RPCError) as error:
                logger.warning("Telegram update failed (%s)", type(error).__name__)

            try:
                await asyncio.wait_for(self.refresh.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _show_page(self, peer) -> None:
        buttons = []
        if self.page > 0:
            buttons.append(Button.inline("‹ Назад", f"roster:{self.page - 1}"))
        if self.page + 1 < len(self.pages):
            buttons.append(Button.inline("Далее ›", f"roster:{self.page + 1}"))
        await self._edit_status(peer, self.pages[self.page], buttons or ReplyInlineMarkup([]))

    async def on_roster_page(self, event: events.CallbackQuery.Event) -> None:
        if event.chat_id != self.config.chat_id or event.message_id != self.config.message_id:
            return
        page = int(event.pattern_match.group(1))
        await event.answer()
        async with self.page_lock:
            if page >= len(self.pages) or page == self.page:
                return
            previous_page = self.page
            self.page = page
            try:
                await self._show_page(await self._target())
            except errors.MessageNotModifiedError:
                pass
            except (OSError, ValueError, errors.RPCError) as error:
                self.page = previous_page
                logger.warning("Cannot open roster page (%s)", type(error).__name__)

    async def _edit_status(self, peer, message: str, buttons) -> None:
        text, entities = html.parse(message)
        try:
            await self.client.edit_message(
                peer, self.config.message_id, text, formatting_entities=entities, buttons=buttons,
            )
        except (
            errors.DocumentInvalidError, errors.EmojiInvalidError,
            errors.EntityBoundsInvalidError, errors.PremiumAccountRequiredError,
        ):
            if not any(isinstance(entity, MessageEntityCustomEmoji) for entity in entities):
                raise
            self.custom_emoji_rejected = True
            logger.warning("Telegram отклонил эмодзи набора в сообщении с онлайном.")
            text, plain = html.parse(PLAYER_HEAD_TAG.sub("", message))
            plain = [
                entity for entity in plain if not isinstance(entity, MessageEntityCustomEmoji)
            ]
            await self.client.edit_message(
                peer, self.config.message_id, text, formatting_entities=plain, buttons=buttons,
            )

    async def on_message(self, event: events.NewMessage.Event) -> None:
        if event.chat_id == self.config.chat_id and self.target_peer is None:
            peer = await event.get_input_chat()
            if peer is not None:
                self.target_peer = peer
                self.refresh.set()

        if (
            not self.config.allows_command(event.chat_id, event.sender_id)
            or event.fwd_from is not None
            or event.via_bot_id is not None
        ):
            return
        match = COMMAND.fullmatch(event.raw_text.strip())
        if match is None:
            return
        command, username = match.groups()
        if username is not None and username.lower() != self.username:
            return
        if command == "status":
            self.refresh.set()
            await event.reply("Запросил обновление сообщения с онлайном.", parse_mode=None)
            return
        if command == "emoji_sync":
            result = "Публикация набора эмодзи отключена в конфигурации."
            if self.emoji_pack is not None:
                try:
                    result = await self.emoji_pack.sync(self.username)
                except (EmojiPackError, OSError, ValueError) as error:
                    logger.warning("Не удалось обновить набор эмодзи: %s", error)
                    result = "Не удалось обновить набор эмодзи. Проверьте журнал бота."
                self.refresh.set()
            await event.reply(result, parse_mode=None, link_preview=False)
            return

        game, action = command.rsplit("_", 1)
        control = self.tunnel if game == "tunnel" else self.controls.get(game)
        if control is None:
            message = (
                "Управление туннелем отключено в конфигурации."
                if game == "tunnel"
                else "Управление этой игрой отключено в конфигурации."
            )
            await event.reply(message, parse_mode=None)
            return
        result = await control.run(action)
        self.refresh.set()
        await event.reply(result, parse_mode=None)

    async def run(self, token: str, *, on_ready: Callable[[], None] | None = None) -> None:
        await self.client.start(bot_token=token)
        account = await self.client.get_me()
        token_id = int(token.split(":", 1)[0])
        if not account.bot or account.id != token_id:
            raise ValueError("Telethon-сессия принадлежит другому аккаунту.")
        self.username = account.username.lower()
        self.client.add_event_handler(self.on_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(
            self.on_roster_page, events.CallbackQuery(pattern=rb"^roster:(\d+)$")
        )
        background = [asyncio.create_task(self._publish())]
        if self.emoji_pack is not None:
            background.append(asyncio.create_task(
                self.emoji_pack.run_periodically(self.username, self.refresh)
            ))
        try:
            if on_ready is not None:
                on_ready()
            await self.client.run_until_disconnected()
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await self.client.disconnect()
