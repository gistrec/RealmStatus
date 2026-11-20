"""Configuration for the local Telethon process."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CHAT_ID = -1003410760885


@dataclass(frozen=True)
class LocalConfig:
    path: Path
    data: dict[str, Any]
    using_template: bool

    @classmethod
    def load(cls) -> "LocalConfig":
        custom_path = os.getenv("GAMES_CONFIG")
        path = PROJECT_DIR / Path(custom_path or "games.toml").expanduser()
        using_template = not custom_path and not path.exists()
        if using_template:
            path = PROJECT_DIR / "games.example.toml"
        path = path.resolve()
        try:
            with path.open("rb") as stream:
                return cls(path, tomllib.load(stream), using_template)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"Не удалось прочитать конфигурацию {path}: {error}") from error


@dataclass(frozen=True)
class TelegramSettings:
    chat_id: int
    message_id: int
    control_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    allow_all_users: bool
    interval: int
    chat_username: str | None
    show_offline_players: bool = True

    @classmethod
    def load(cls, config: LocalConfig) -> "TelegramSettings":
        table = config.data.get("telegram")
        if table is not None and not isinstance(table, dict):
            raise ValueError("telegram: ожидается таблица TOML.")
        show_offline_players = (table or {}).get("show_offline_players", True)
        if type(show_offline_players) is not bool:
            raise ValueError("telegram.show_offline_players: ожидается true или false.")
        if config.using_template or table is None:
            chat_id = int(os.getenv("TELEGRAM_CHAT_ID", str(DEFAULT_CHAT_ID)))
            table = {
                "chat_id": chat_id,
                "message_id": int(os.getenv("TELEGRAM_MESSAGE_ID", "38")),
                "control_chat_ids": [
                    int(os.getenv("TELEGRAM_CONTROL_CHAT_ID") or chat_id)
                ],
                "allowed_user_ids": [
                    int(value)
                    for value in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
                    if value.strip()
                ],
                "update_interval_seconds": int(os.getenv("UPDATE_INTERVAL_SECONDS", "60")),
                "chat_username": os.getenv("TELEGRAM_CHAT_USERNAME", ""),
            }
        chat_id = table.get("chat_id", DEFAULT_CHAT_ID)
        message_id = table.get("message_id", 38)
        interval = table.get("update_interval_seconds", 60)
        control_chat_ids = table.get("control_chat_ids", [chat_id])
        allowed_user_ids = table.get("allowed_user_ids", [])
        allow_all_users = table.get("allow_all_users", False)
        chat_username = table.get("chat_username", "")

        if type(chat_id) is not int or chat_id == 0:
            raise ValueError("telegram.chat_id: ожидается ненулевой числовой ID чата.")
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("telegram.message_id: ожидается положительный ID сообщения.")
        if type(interval) is not int or interval < 30:
            raise ValueError("telegram.update_interval_seconds: ожидается интервал от 30 секунд.")
        if type(allow_all_users) is not bool:
            raise ValueError("telegram.allow_all_users: ожидается true или false.")
        if not isinstance(chat_username, str):
            raise ValueError("telegram.chat_username: ожидается строка.")
        if not isinstance(control_chat_ids, list) or any(
            type(chat) is not int or chat == 0 for chat in control_chat_ids
        ):
            raise ValueError("telegram.control_chat_ids: ожидается список ненулевых ID чатов.")
        if not isinstance(allowed_user_ids, list) or any(
            type(user) is not int or user <= 0 for user in allowed_user_ids
        ):
            raise ValueError("telegram.allowed_user_ids: ожидается список положительных ID пользователей.")

        return cls(
            chat_id=chat_id,
            message_id=message_id,
            control_chat_ids=frozenset(control_chat_ids),
            allowed_user_ids=frozenset(allowed_user_ids),
            allow_all_users=allow_all_users,
            interval=interval,
            chat_username=chat_username.strip() or None,
            show_offline_players=show_offline_players,
        )

    def allows_command(
        self, chat_id: int | None, user_id: int | None, *, is_private: bool = False
    ) -> bool:
        if user_id is None or user_id <= 0:
            return False
        allowed_user = user_id in self.allowed_user_ids
        if is_private and chat_id == user_id and allowed_user:
            return True
        return chat_id in self.control_chat_ids and (self.allow_all_users or allowed_user)


def load_environment() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    os.environ.setdefault("MYSQL_DRIVER", "pymysql")


def required(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise ValueError(f"Заполните {key} в .env.")
    return value
