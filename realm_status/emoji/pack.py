"""Publish static custom emoji through the Telegram Bot API."""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from .images import ICON_EMOJI, EmojiImage, EmojiImages
from .settings import EmojiSettings

logger = logging.getLogger(__name__)
PACK_LIMIT = 200


class EmojiPackError(RuntimeError):
    def __init__(self, message: str, *, retry_after: int = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TelegramEmojiAPI:
    def __init__(self, token: str, owner_id: int, name: str) -> None:
        self.token = token
        self.owner_id = owner_id
        self.name = name

    def call(
        self, method: str, data: dict[str, Any], image: bytes | None = None
    ) -> Any:
        files = {"image": ("emoji.png", image, "image/png")} if image is not None else None
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                data=data,
                files=files,
                timeout=(5, 20),
                allow_redirects=False,
            )
            body = response.json()
        except (requests.RequestException, ValueError) as error:
            raise EmojiPackError(f"Telegram API недоступен ({type(error).__name__}).") from None
        if not body.get("ok"):
            reason = str(body.get("description", "неизвестная ошибка"))
            raise EmojiPackError(
                f"{method}: {reason.replace(self.token, '[token]')}",
                retry_after=body.get("parameters", {}).get("retry_after", 0),
            )
        return body["result"]

    def get(self) -> dict[str, Any] | None:
        try:
            return self.call("getStickerSet", {"name": self.name})
        except EmojiPackError as error:
            if "STICKERSET_INVALID" in str(error).upper():
                return None
            raise

    def upload(self, image: EmojiImage, content: bytes, *, title: str | None = None) -> None:
        sticker = {
            "sticker": "attach://image",
            "format": "static",
            "emoji_list": [image.alt],
            "keywords": [image.keyword[:48]],
        }
        data = {"name": self.name, "user_id": self.owner_id}
        if title is not None:
            data.update(title=title, sticker_type="custom_emoji", stickers=json.dumps([sticker]))
            self.call("createNewStickerSet", data, content)
        else:
            data["sticker"] = json.dumps(sticker)
            self.call("addStickerToSet", data, content)


class EmojiPack:
    def __init__(self, settings: EmojiSettings, work_dir: Path, token: str) -> None:
        self.settings = settings
        self.work_dir = work_dir
        self.images = EmojiImages(settings, work_dir)
        self._token = token
        self.icons: dict[str, tuple[int, str]] = {}
        self.lock = asyncio.Lock()
        self.retry_at = 0.0

    def _publish(self, name: str) -> tuple[dict[str, tuple[int, str]], int, bool]:
        api = TelegramEmojiAPI(self._token, self.settings.owner_id, name)
        images = self.images.prepare()
        sticker_set = api.get()
        if sticker_set is not None and sticker_set["sticker_type"] != "custom_emoji":
            raise EmojiPackError("Это имя занято набором другого типа.")

        state = self.work_dir / f"{name.lower()}.json"
        published = set()
        if sticker_set is not None and state.is_file():
            published = set(json.loads(state.read_bytes()))
        size = len(sticker_set["stickers"]) if sticker_set is not None else 0
        uploaded = 0
        full = False
        for image in images:
            content = image.path.read_bytes()
            digest = hashlib.sha256(image.alt.encode() + content).hexdigest()
            if digest in published:
                continue
            if size >= PACK_LIMIT:
                full = True
                break
            title = self.settings.title if sticker_set is None and size == 0 else None
            api.upload(image, content, title=title)
            published.add(digest)
            tmp_path = state.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(sorted(published)), encoding="utf-8")
            tmp_path.replace(state)
            uploaded += 1
            size += 1

        if uploaded:
            sticker_set = api.get()
        if sticker_set is None:
            raise EmojiPackError("Telegram пока не вернул созданный набор.")
        icons = {}
        for sticker in sticker_set["stickers"]:
            alt = sticker.get("emoji")
            document_id = sticker.get("custom_emoji_id")
            if alt in ICON_EMOJI and document_id:
                icons[ICON_EMOJI[alt]] = (int(document_id), alt)
        return icons, uploaded, full

    async def sync(self, username: str) -> str:
        name = f"{self.settings.prefix}_by_{username}"
        if len(name) > 64 or "__" in name:
            raise EmojiPackError("Недопустимое имя набора. Проверьте emoji_pack.prefix и имя бота.")
        if self.lock.locked():
            return "Набор эмодзи уже обновляется."
        if time.monotonic() < self.retry_at:
            return "Telegram ограничил частоту обновлений набора. Попробуйте позже."
        async with self.lock:
            try:
                self.icons, uploaded, full = await asyncio.to_thread(self._publish, name)
            except EmojiPackError as error:
                self.retry_at = time.monotonic() + error.retry_after
                raise
        limit = "\nВ наборе достигнут лимит Telegram." if full else ""
        return f"Набор эмодзи: https://t.me/addemoji/{name}\nДобавлено: {uploaded}{limit}"

    async def run_periodically(self, username: str, refresh: asyncio.Event) -> None:
        while True:
            previous_icons = self.icons
            try:
                await self.sync(username)
                if self.icons != previous_icons:
                    refresh.set()
            except (EmojiPackError, OSError, ValueError) as error:
                logger.warning("Не удалось обновить набор эмодзи: %s", error)
            delay = max(self.settings.refresh_seconds, self.retry_at - time.monotonic())
            await asyncio.sleep(delay)
