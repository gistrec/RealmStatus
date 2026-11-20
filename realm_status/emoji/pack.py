"""Publish static custom emoji through the Telegram Bot API."""

import asyncio
import hashlib
import json
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from telethon import TelegramClient, errors, functions, types

from .images import EmojiImage, EmojiImages
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

    def upload(
        self, asset: "PackAsset", *, title: str | None = None, old_file: str | None = None
    ) -> None:
        sticker = {
            "sticker": "attach://image",
            "format": "static",
            "emoji_list": [asset.image.alt],
            "keywords": asset.keywords,
        }
        data = {"name": self.name, "user_id": self.owner_id}
        if title is not None:
            data.update(title=title, sticker_type="custom_emoji", stickers=json.dumps([sticker]))
            self.call("createNewStickerSet", data, asset.content)
        else:
            data["sticker"] = json.dumps(sticker)
            method = "addStickerToSet"
            if old_file is not None:
                data["old_sticker"] = old_file
                method = "replaceStickerInSet"
            self.call(method, data, asset.content)

    def label(self, asset: "PackAsset", file_id: str) -> None:
        self.call(
            "setStickerKeywords",
            {"sticker": file_id, "keywords": json.dumps(asset.keywords)},
        )


@dataclass(frozen=True)
class PackAsset:
    image: EmojiImage
    content: bytes
    marker: str
    digest: str

    @classmethod
    def read(cls, image: EmojiImage) -> "PackAsset":
        content = image.path.read_bytes()
        key = hashlib.sha256(image.path.stem.encode()).hexdigest()[:20]
        digest = hashlib.sha256(image.alt.encode() + content).hexdigest()
        return cls(image, content, f"rs{key}{digest[:20]}", digest)

    @property
    def key(self) -> str:
        return self.marker[:22]

    @property
    def keywords(self) -> list[str]:
        return [self.marker, self.image.keyword[:20]]

    @property
    def legacy_key(self) -> tuple[str, str]:
        keyword = self.image.keyword[:48].casefold() if self.image.game else self.image.alt
        return self.image.alt, keyword


class RemotePack:
    def __init__(self, sticker_set) -> None:
        self.size = len(sticker_set.documents) if sticker_set else 0
        self.marked: dict[str, tuple[str, int]] = {}
        self.legacy: dict[tuple[str, str], set[int]] = defaultdict(set)
        if sticker_set is None:
            return

        keywords = {entry.document_id: entry.keyword for entry in sticker_set.keywords}
        for document in sticker_set.documents:
            alt = next(
                (
                    attribute.alt for attribute in document.attributes
                    if isinstance(attribute, types.DocumentAttributeCustomEmoji)
                ),
                None,
            )
            if alt is None:
                continue
            words = [word.casefold() for word in keywords.get(document.id, [])]
            marker = next(
                (
                    word for word in words
                    if len(word) == 42 and word.startswith("rs")
                    and all(char in "0123456789abcdef" for char in word[2:])
                ),
                None,
            )
            if marker is not None:
                self.marked[marker[:22]] = (marker, document.id)
                continue
            self.legacy[alt, alt].add(document.id)
            for word in words:
                self.legacy[alt, word].add(document.id)

    def find(self, asset: PackAsset, unique_legacy_key: bool) -> tuple[str | None, int | None]:
        if asset.key in self.marked:
            return self.marked[asset.key]
        matches = self.legacy.get(asset.legacy_key, set())
        if unique_legacy_key and len(matches) == 1:
            return None, next(iter(matches))
        return None, None


class EmojiPack:
    def __init__(
        self, settings: EmojiSettings, work_dir: Path, token: str, client: TelegramClient
    ) -> None:
        self.settings = settings
        self.work_dir = work_dir
        self.images = EmojiImages(settings, work_dir)
        self._token = token
        self._client = client
        self.icons: dict[str, tuple[int, str]] = {}
        self.heads: dict[tuple[str, str], int] = {}
        self.lock = asyncio.Lock()
        self.retry_at = 0.0

    async def _remote(self, name: str) -> RemotePack:
        try:
            result = await self._client(functions.messages.GetStickerSetRequest(
                types.InputStickerSetShortName(name), hash=0
            ))
        except errors.StickersetInvalidError:
            return RemotePack(None)
        except errors.FloodWaitError as error:
            raise EmojiPackError("Telegram ограничил частоту обновлений набора.", retry_after=error.seconds) from error
        except errors.RPCError as error:
            raise EmojiPackError(f"Не удалось получить набор Telegram ({type(error).__name__}).") from error
        if not getattr(result.set, "emojis", False):
            raise EmojiPackError("Это имя занято набором другого типа.")
        return RemotePack(result)

    def _prepare(self) -> list[PackAsset]:
        return [PackAsset.read(image) for image in self.images.prepare()]

    async def _publish(self, name: str, assets: list[PackAsset]) -> tuple[int, int, bool]:
        remote = await self._remote(name)
        legacy_keys = Counter(asset.legacy_key for asset in assets)
        legacy_state = self.work_dir / f"{name.lower()}.json"
        legacy_digests = (
            set(json.loads(legacy_state.read_bytes())) if legacy_state.is_file() else set()
        )
        api = TelegramEmojiAPI(self._token, self.settings.owner_id, name)
        files = None
        size = remote.size
        added = replaced = 0
        full = False
        relabeled = False
        for asset in assets:
            marker, old_id = remote.find(asset, legacy_keys[asset.legacy_key] == 1)
            if marker == asset.marker:
                continue
            old_file = None
            if old_id is not None:
                if files is None:
                    sticker_set = await asyncio.to_thread(api.get)
                    if sticker_set is None:
                        raise EmojiPackError("Набор исчез во время синхронизации.")
                    files = {
                        int(sticker["custom_emoji_id"]): sticker["file_id"]
                        for sticker in sticker_set["stickers"] if sticker.get("custom_emoji_id")
                    }
                old_file = files.get(old_id)
                if old_file is None:
                    raise EmojiPackError("Telegram вернул разные версии набора. Повторю проверку позже.")
                if marker is None and asset.digest in legacy_digests:
                    await asyncio.to_thread(api.label, asset, old_file)
                    relabeled = True
                    continue
            elif size >= PACK_LIMIT:
                full = True
                continue

            title = self.settings.title if size == 0 else None
            await asyncio.to_thread(api.upload, asset, title=title, old_file=old_file)
            if old_file is not None:
                replaced += 1
            else:
                added += 1
                size += 1

        if added or replaced or relabeled:
            remote = await self._remote(name)

        icons = {}
        heads = {}
        for asset in assets:
            _, document_id = remote.find(asset, legacy_keys[asset.legacy_key] == 1)
            if document_id is None:
                continue
            image = asset.image
            if image.game is None:
                icons[image.keyword] = (document_id, image.alt)
            else:
                heads[image.game, image.keyword.casefold()] = document_id
        self.icons, self.heads = icons, heads
        return added, replaced, full

    async def sync(self, username: str) -> str:
        name = f"{self.settings.prefix}_by_{username}"
        if len(name) > 64 or "__" in name:
            raise EmojiPackError("Недопустимое имя набора. Проверьте emoji_pack.prefix и имя бота.")
        if self.lock.locked():
            return "Набор эмодзи уже обновляется."
        async with self.lock:
            assets = await asyncio.to_thread(self._prepare)
            if time.monotonic() < self.retry_at:
                return "Локальные головы проверены. Telegram временно ограничил обновления набора."
            try:
                added, replaced, full = await self._publish(name, assets)
            except EmojiPackError as error:
                self.retry_at = time.monotonic() + error.retry_after
                raise
        limit = "\nВ наборе достигнут лимит Telegram." if full else ""
        return f"Набор эмодзи: https://t.me/addemoji/{name}\nДобавлено: {added}, обновлено: {replaced}{limit}"

    async def run_periodically(self, username: str, refresh: asyncio.Event) -> None:
        while True:
            next_sync = time.monotonic() + self.settings.refresh_seconds
            previous = self.icons, self.heads
            try:
                await self.sync(username)
                if (self.icons, self.heads) != previous:
                    refresh.set()
            except (EmojiPackError, OSError, ValueError) as error:
                logger.warning("Не удалось обновить набор эмодзи: %s", error)
            await asyncio.sleep(max(0, next_sync - time.monotonic()))
