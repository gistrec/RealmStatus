"""Configuration for sourcing images and publishing their Telegram pack."""

import re
from dataclasses import dataclass
from pathlib import Path

from ..settings import LocalConfig

PACK_PREFIX = re.compile(r"[a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)*")


@dataclass(frozen=True)
class EmojiSettings:
    enabled: bool
    owner_id: int
    prefix: str
    title: str
    refresh_seconds: int
    source_dir: Path
    minecraft_skins: Path
    terraria_data: Path

    @classmethod
    def load(cls, config: LocalConfig) -> "EmojiSettings":
        table = config.data.get("emoji_pack", {})
        if not isinstance(table, dict):
            raise ValueError("emoji_pack: ожидается таблица TOML.")

        def option(key: str, default):
            value = table.get(key, default)
            if type(value) is not type(default) or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"Проверьте emoji_pack.{key}.")
            return value

        def path(key: str, default: str) -> Path:
            return (config.path.parent / Path(option(key, default)).expanduser()).resolve()

        enabled = option("enabled", False)
        owner_id = option("owner_id", 0)
        prefix = option("prefix", "realmstatus")
        title = option("title", "RealmStatus")
        refresh_seconds = option("refresh_seconds", 60)
        if owner_id < 0 or (enabled and not owner_id):
            raise ValueError("Для включения emoji_pack укажите положительный owner_id.")
        if not PACK_PREFIX.fullmatch(prefix) or not 1 <= len(title) <= 64:
            raise ValueError("Проверьте emoji_pack.prefix и emoji_pack.title.")
        if refresh_seconds < 60:
            raise ValueError("emoji_pack.refresh_seconds должен быть не меньше 60.")
        return cls(
            enabled,
            owner_id,
            prefix,
            title,
            refresh_seconds,
            path("source_directory", "assets/emoji/source"),
            path("minecraft_skins", "../mc.stashkevich.com/plugins/SkinsRestorer/skins"),
            path("terraria_data", "~/Library/Application Support/tMapOnline"),
        )
