"""Run configured Make targets for local servers and the shared FRPC tunnel."""

import asyncio
import logging
import os
import re
import signal
from pathlib import Path
from typing import ClassVar

from .settings import LocalConfig

logger = logging.getLogger(__name__)
MAKE_TARGET = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")


class ControlError(RuntimeError):
    pass


class MakeControl:
    success_messages: ClassVar[dict[str, str]]
    busy_message: ClassVar[str]

    def __init__(self, directory: Path, targets: dict[str, tuple[str, ...]]) -> None:
        self.directory = directory
        self.targets = targets
        self.lock = asyncio.Lock()

    async def _make(self, target: str) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TELEGRAM_", "MYSQL_", "STARDEW_"))
        }
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/make", target,
            cwd=self.directory,
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=240)
        except (TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            await process.wait()
            raise
        if process.returncode:
            raise ControlError(f"make {target} завершился с кодом {process.returncode}.")

    async def run(self, action: str) -> str:
        if action not in self.targets:
            raise ValueError("Неизвестная операция.")
        if self.lock.locked():
            return self.busy_message
        async with self.lock:
            try:
                for target in self.targets[action]:
                    await self._make(target)
            except (OSError, ControlError, TimeoutError) as error:
                logger.warning("%s / %s: %s", self.directory, action, error)
                return "Команда не завершилась успешно. Проверьте журнал на Mac."
        return self.success_messages[action]

    @staticmethod
    def target_setting(
        settings: dict[str, object], key: str, section: str, default: str
    ) -> str:
        target = settings.get(key, default)
        if not isinstance(target, str) or not MAKE_TARGET.fullmatch(target):
            raise ValueError(f"{section}.{key}: укажите имя одной Make-цели.")
        return target


class ServerControl(MakeControl):
    success_messages = {
        "start": "Сервер запущен.",
        "stop": "Сервер остановлен.",
    }
    busy_message = "Для этого сервера уже выполняется команда."

    def __init__(self, directory: Path, *, start: str = "start", stop: str = "stop") -> None:
        super().__init__(directory, {"start": (start,), "stop": (stop,)})


class TunnelControl(MakeControl):
    success_messages = {
        "start": "Туннель запущен.",
        "stop": "Туннель остановлен.",
        "restart": "Туннель перезапущен.",
    }
    busy_message = "Для туннеля уже выполняется команда."

    @classmethod
    def load(cls, config: LocalConfig) -> "TunnelControl | None":
        settings = config.data.get("tunnel", {})
        if not isinstance(settings, dict):
            raise ValueError("tunnel: ожидается таблица TOML.")
        enabled = settings.get("enabled", False)
        if type(enabled) is not bool:
            raise ValueError("tunnel.enabled: ожидается true или false.")
        if not enabled:
            return None
        directory = settings.get("directory")
        if not isinstance(directory, str) or not directory.strip():
            raise ValueError("tunnel.directory: укажите папку с Make-целями FRPC.")
        start = cls.target_setting(settings, "start_target", "tunnel", "tunnel-up")
        stop = cls.target_setting(settings, "stop_target", "tunnel", "tunnel-down")
        restart = (stop, start)
        if "restart_target" in settings:
            restart = (cls.target_setting(settings, "restart_target", "tunnel", ""),)
        return cls(
            (config.path.parent / Path(directory).expanduser()).resolve(),
            {"start": (start,), "stop": (stop,), "restart": restart},
        )
