"""Collect original icons and player heads, then render Telegram-ready PNGs."""

import base64
import json
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from uuid import UUID

import requests
from PIL import Image

from ..status import PLAYER_EMOJI
from .settings import EmojiSettings

logger = logging.getLogger(__name__)
GAME_ICONS = {
    "minecraft.icns": ("minecraft", "🎮"),
    "terraria.icns": ("terraria", "🌳"),
    "stardew.png": ("stardew", "🌾"),
}
SKIN_PATH = re.compile(r"/texture/([a-f0-9]{32,64})")
AVATAR_PATH = re.compile(r"/player-avatars/([a-zA-Z0-9_-]+\.png)")
MAX_TEXTURE_BYTES = 1_000_000


@dataclass(frozen=True)
class EmojiImage:
    path: Path
    alt: str
    keyword: str
    game: str | None = None


class EmojiImages:
    def __init__(self, settings: EmojiSettings, work_dir: Path) -> None:
        self.settings = settings
        self.work_dir = work_dir

    def prepare(self) -> list[EmojiImage]:
        images = []
        for filename, (game, alt) in GAME_ICONS.items():
            path = self.settings.source_dir / filename
            with Image.open(path) as icon:
                images.append(self._save(icon, game, alt, game))
        images.extend(self._minecraft_heads())
        images.extend(self._terraria_heads())
        return images

    def _save(
        self, image: Image.Image, key: str, alt: str, keyword: str, *, game: str | None = None
    ) -> EmojiImage:
        image = image.convert("RGBA")
        bounds = image.getbbox()
        if not bounds:
            raise ValueError("Изображение полностью прозрачное.")
        image = image.crop(bounds)
        if game is not None and max(image.size) <= 96:
            factor = 96 // max(image.size)
            image = image.resize(
                (image.width * factor, image.height * factor), Image.Resampling.NEAREST
            )
        else:
            image.thumbnail((96, 96), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (100, 100))
        canvas.paste(image, ((100 - image.width) // 2, (100 - image.height) // 2))
        output = self.work_dir / "png" / f"{key}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        buffer = BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        content = buffer.getvalue()
        if not output.exists() or output.read_bytes() != content:
            with NamedTemporaryFile(dir=output.parent, delete=False) as temporary:
                temporary.write(content)
            Path(temporary.name).replace(output)
        return EmojiImage(output, alt, keyword, game)

    def _texture(self, value: str) -> bytes:
        decoded = json.loads(base64.b64decode(value, validate=True))
        target = urlsplit(decoded["textures"]["SKIN"]["url"])
        match = SKIN_PATH.fullmatch(target.path)
        if target.hostname != "textures.minecraft.net" or not match:
            raise ValueError("Неизвестный адрес текстуры Minecraft.")
        cache = self.work_dir / "skins" / f"{match[1]}.png"
        if cache.is_file():
            return cache.read_bytes()
        with requests.get(
            f"https://textures.minecraft.net/texture/{match[1]}",
            timeout=(4, 8),
            stream=True,
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise OSError(f"Сервер текстур вернул HTTP {response.status_code}.")
            raw = response.raw.read(MAX_TEXTURE_BYTES + 1)
        if len(raw) > MAX_TEXTURE_BYTES:
            raise ValueError("Текстура Minecraft превышает ожидаемый размер.")
        with Image.open(BytesIO(raw)) as texture:
            texture.verify()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
        return raw

    def _minecraft_heads(self) -> list[EmojiImage]:
        images = []
        for file in sorted(self.settings.minecraft_skins.glob("*.playerskin")):
            try:
                player = json.loads(file.read_bytes())
                player_id = UUID(player["uniqueId"]).hex
                name = str(player.get("lastKnownName") or player_id)
                with Image.open(BytesIO(self._texture(player["value"]))) as texture:
                    if texture.width < 64 or texture.width % 64:
                        raise ValueError("Неподдерживаемый размер текстуры Minecraft.")
                    scale = texture.width // 64
                    if texture.height not in (32 * scale, 64 * scale):
                        raise ValueError("Неподдерживаемый размер текстуры Minecraft.")
                    skin = texture.convert("RGBA")
                    head = skin.crop(tuple(point * scale for point in (8, 8, 16, 16)))
                    hat = skin.crop(tuple(point * scale for point in (40, 8, 48, 16)))
                    # Legacy 64×32 skins can store an unused opaque hat region.
                    if texture.height == 64 * scale or hat.getchannel("A").getextrema()[0] < 255:
                        head.alpha_composite(hat)
                images.append(self._save(
                    head, f"minecraft-{player_id}", PLAYER_EMOJI, name, game="minecraft"
                ))
            except (OSError, ValueError, KeyError, TypeError, requests.RequestException) as error:
                logger.warning("Пропущен скин Minecraft %s (%s).", file.stem, type(error).__name__)
        return images

    def _terraria_heads(self) -> list[EmojiImage]:
        history = self.settings.terraria_data / "player-history.json"
        if not history.is_file():
            return []
        try:
            players = json.loads(history.read_bytes())["players"]
            if not isinstance(players, dict):
                raise ValueError("Некорректная история Terraria.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning("История Terraria недоступна (%s).", type(error).__name__)
            return []
        images = []
        for player in players.values():
            if not isinstance(player, dict):
                continue
            match = AVATAR_PATH.fullmatch(urlsplit(str(player.get("avatar_url") or "")).path)
            if not match:
                continue
            filename = match[1]
            try:
                with Image.open(self.settings.terraria_data / "player-avatars" / filename) as head:
                    images.append(self._save(
                        head, f"terraria-{Path(filename).stem}", PLAYER_EMOJI,
                        str(player.get("name") or "Terraria"), game="terraria",
                    ))
            except (OSError, ValueError) as error:
                logger.warning("Пропущен аватар Terraria %s (%s).", filename, type(error).__name__)
        return images
