"""Load game profiles and connect the configured status sources and controls."""

import os
import re
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from stardew_valley import StardewValleyService

from .control import ServerControl
from .history import PlayerRoster
from .settings import LocalConfig
from .sources.legacy import MinecraftRealms, StardewValley
from .sources.local import MinecraftServer, TerrariaServer
from .sources.minecraft_history import MinecraftHistory
from .status import Game, StatusBoard, StatusSource

GAME_ID = re.compile(r"[a-z][a-z0-9_]{0,25}")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SOURCE_EMOJI_KEYS = {
    "minecraft_java": "minecraft",
    "minecraft_realms": "minecraft",
    "terraria_viewer": "terraria",
    "stardew_api": "stardew",
}


@dataclass(frozen=True)
class GameProfile:
    id: str
    title: str
    icon: str
    source: str
    options: dict[str, Any]
    config_dir: Path

    @property
    def name(self) -> str:
        return f"games.{self.id}.sources.{self.source}"

    def value(self, key: str, expected: type) -> Any:
        value = self.options.get(key)
        if type(value) is not expected or (expected is str and not value.strip()):
            raise ValueError(f"{self.name}.{key}: ожидается {expected.__name__}.")
        return value

    def port(self) -> int:
        port = self.value("port", int)
        if not 1 <= port <= 65535:
            raise ValueError(f"{self.name}.port: ожидается порт от 1 до 65535.")
        return port

    def realm_id(self) -> int:
        world_id = self.value("world_id", int)
        if world_id <= 0:
            raise ValueError(f"{self.name}.world_id: ожидается положительный ID.")
        return world_id

    def make_source(self) -> StatusSource:
        if self.source == "minecraft_java":
            host = self.value("host", str)
            directory = self.options.get("history_directory")
            if directory is None and host in LOCAL_HOSTS and "control" in self.options:
                control = _table(self.options["control"], f"{self.name}.control")
                directory = control.get("directory")
            history = None
            if directory is not None:
                root = self._local_path(directory, "history_directory")
                world = self.options.get("world_directory")
                history = MinecraftHistory(root, self._local_path(world, "world_directory") if world else None)
            return MinecraftServer(host, self.port(), history)
        if self.source == "minecraft_realms":
            return MinecraftRealms(self.realm_id())
        if self.source == "terraria_viewer":
            url = self.value("url", str)
            _validate_url(url, f"{self.name}.url")
            host = self.value("host", str)
            directory = self.options.get("history_directory")
            if directory is None and host in LOCAL_HOSTS:
                directory = "~/Library/Application Support/tMapOnline"
            history_dir = self._local_path(directory, "history_directory") if directory is not None else None
            return TerrariaServer(url, self.port(), host=host, history_dir=history_dir)
        if self.source == "stardew_api":
            base_url = os.getenv("STARDEW_API_BASE_URL") or ""
            if "base_url" in self.options:
                base_url = self.value("base_url", str)
            _validate_url(base_url, f"{self.name}.base_url или STARDEW_API_BASE_URL")
            token = os.getenv("STARDEW_API_TOKEN")
            if not token:
                raise ValueError("Заполните STARDEW_API_TOKEN в .env.")
            return StardewValley(StardewValleyService(base_url=base_url, api_token=token))
        raise ValueError(f"games.{self.id}.source: неизвестный источник {self.source!r}.")

    def _local_path(self, value: object, key: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{self.name}.{key}: укажите путь к папке.")
        return (self.config_dir / Path(value).expanduser()).resolve()

    def history_key(self) -> str:
        parts = tuple(self.options.get(key) for key in ("host", "port", "url", "world_id", "world_directory"))
        if self.source == "stardew_api":
            parts += (self.options.get("base_url") or os.getenv("STARDEW_API_BASE_URL"),)
        identity = sha256(repr(parts).encode()).hexdigest()[:16]
        return f"{self.id}:{self.source}:{identity}"

    def make_control(self) -> ServerControl | None:
        if "control" not in self.options:
            return None
        if (
            self.source not in {"minecraft_java", "terraria_viewer"}
            or self.value("host", str) not in LOCAL_HOSTS
        ):
            raise ValueError(f"{self.name}.control поддерживается только для локального сервера.")

        section = f"{self.name}.control"
        table = _table(self.options["control"], section)
        directory = Path(_text(table, "directory", section)).expanduser()
        return ServerControl(
            (self.config_dir / directory).resolve(),
            start=ServerControl.target_setting(table, "start_target", section, "start"),
            stop=ServerControl.target_setting(table, "stop_target", section, "stop"),
        )


@dataclass(frozen=True)
class GameCatalog:
    profiles: tuple[GameProfile, ...]

    @classmethod
    def load(cls, config: LocalConfig) -> "GameCatalog":
        games = _table(config.data.get("games"), "games")
        if config.using_template:
            _apply_legacy_environment(games)
        profiles = []
        for game_id, raw_game in games.items():
            if game_id == "tunnel" or not GAME_ID.fullmatch(game_id):
                raise ValueError(f"Недопустимый ID игры: {game_id!r}.")
            name = f"games.{game_id}"
            game = _table(raw_game, name)
            enabled = game.get("enabled", True)
            if type(enabled) is not bool:
                raise ValueError(f"{name}.enabled: ожидается true или false.")
            if not enabled:
                continue
            source = _text(game, "source", name)
            sources = _table(game.get("sources"), f"{name}.sources")
            options = _table(sources.get(source), f"{name}.sources.{source}")
            profiles.append(
                GameProfile(
                    game_id,
                    _text(game, "title", name),
                    _text(game, "icon", name),
                    source,
                    options,
                    config.path.parent,
                )
            )
        return cls(tuple(profiles))

    def build(
        self, roster: PlayerRoster | None = None, *, poll_interval: int = 60,
        show_offline_players: bool = True,
    ) -> tuple[StatusBoard, dict[str, ServerControl]]:
        games = []
        controls = {}
        for profile in self.profiles:
            games.append(Game(
                profile.title, profile.icon, profile.make_source(),
                SOURCE_EMOJI_KEYS[profile.source], profile.history_key(),
            ))
            control = profile.make_control()
            if control is not None:
                controls[profile.id] = control
        board = StatusBoard(
            tuple(games), roster, poll_interval=poll_interval, show_offline_players=show_offline_players
        )
        return board, controls

    def realms_profile(self, game_id: str | None) -> GameProfile:
        matches = [
            profile for profile in self.profiles
            if profile.source == "minecraft_realms"
            and (game_id is None or profile.id == game_id)
        ]
        if len(matches) != 1:
            raise ValueError(
                "Нужна одна включённая игра с источником minecraft_realms. "
                "Если их несколько, укажите её ID после --realms-login."
            )
        return matches[0]


def _table(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}: ожидается таблица TOML.")
    return value


def _text(table: dict[str, Any], key: str, name: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}.{key}: ожидается непустая строка.")
    return value


def _validate_url(value: str, name: str) -> None:
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError(f"{name}: ожидается URL http:// или https://.")


def _apply_legacy_environment(games: dict[str, Any]) -> None:
    """Honor the previous .env options until a user supplies an explicit TOML file."""
    mode = os.getenv("MINECRAFT_SOURCE", "local")
    if mode not in {"local", "realms"}:
        raise ValueError("MINECRAFT_SOURCE должен быть local или realms.")
    if mode == "realms":
        games["minecraft"]["source"] = "minecraft_realms"
    if os.getenv("STARDEW_ENABLED") == "1":
        games["stardew"]["enabled"] = True

    java = games["minecraft"]["sources"]["minecraft_java"]
    realms = games["minecraft"]["sources"]["minecraft_realms"]
    terraria = games["terraria"]["sources"]["terraria_viewer"]
    overrides = (
        (java, "port", "MINECRAFT_PORT", int),
        (java["control"], "directory", "MINECRAFT_PROJECT", str),
        (realms, "world_id", "MINECRAFT_REALM_ID", int),
        (terraria, "port", "TERRARIA_PORT", int),
        (terraria, "url", "TERRARIA_STATUS_URL", str),
        (terraria["control"], "directory", "TERRARIA_PROJECT", str),
    )
    for table, key, variable, convert in overrides:
        value = os.getenv(variable)
        if value is not None and value != "":
            table[key] = convert(value)
