"""Start and stop the local bot without signalling an unrelated process."""

import argparse
import asyncio
import fcntl
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path

from .settings import PROJECT_DIR

STATE = PROJECT_DIR / ".state"
LOG = STATE / "bot.log"
START_TIMEOUT = 30
STOP_TIMEOUT = 60


@contextmanager
def bot_lock(state: Path):
    state.mkdir(mode=0o700, exist_ok=True)
    with (state / "bot.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("RealmStatus уже запущен.") from error
        yield


class BotRuntime:
    def __init__(self, state: Path = STATE) -> None:
        self.endpoint = state / "bot.sock"
        self.phase = "starting"
        self.stop = asyncio.Event()

    def mark_ready(self) -> None:
        self.phase = "ready"

    async def _command(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            command = await asyncio.wait_for(reader.readexactly(1), timeout=2)
            if command == b"x":
                self.phase = "stopping"
                self.stop.set()
            elif command != b"?":
                return
            writer.write(self.phase.encode("ascii"))
            await writer.drain()
        except (TimeoutError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def run(self, bot: Callable[[], Awaitable[None]]) -> None:
        self.endpoint.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._command, path=self.endpoint)
        try:
            async with server:
                bot_task = asyncio.create_task(bot())
                stop_task = asyncio.create_task(self.stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        (bot_task, stop_task), return_when=asyncio.FIRST_COMPLETED
                    )
                    if bot_task in done:
                        await bot_task
                finally:
                    bot_task.cancel()
                    stop_task.cancel()
                    await asyncio.gather(bot_task, stop_task, return_exceptions=True)
        finally:
            self.endpoint.unlink(missing_ok=True)


def _running(state: Path) -> bool:
    try:
        with bot_lock(state):
            return False
    except RuntimeError:
        return True


def _request(state: Path, command: bytes) -> str | None:
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(1)
            client.connect(str(state / "bot.sock"))
            client.sendall(command)
            return client.recv(32).decode("ascii")
    except (FileNotFoundError, ConnectionError, TimeoutError):
        return None


def _wait_ready(process: subprocess.Popen | None, state: Path) -> str:
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if _request(state, b"?") == "ready":
            return "RealmStatus запущен."
        if (process is None or process.poll() is not None) and not _running(state):
            raise RuntimeError(f"RealmStatus завершился при запуске. Лог: {LOG}")
        time.sleep(0.2)
    raise RuntimeError(f"Бот не подтвердил запуск за {START_TIMEOUT} секунд. Лог: {LOG}")


def start() -> str:
    if _request(STATE, b"?") == "ready":
        return "RealmStatus уже запущен."
    if _running(STATE):
        return _wait_ready(None, STATE)
    with LOG.open("ab") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "realm_status"],
            cwd=PROJECT_DIR,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return _wait_ready(process, STATE)


def stop() -> str:
    deadline = time.monotonic() + STOP_TIMEOUT
    acknowledged = False
    while _running(STATE):
        if not acknowledged:
            acknowledged = _request(STATE, b"x") == "stopping"
        if time.monotonic() >= deadline:
            raise RuntimeError(f"RealmStatus ещё завершает работу. Лог: {LOG}")
        time.sleep(0.2)
    return "RealmStatus остановлен." if acknowledged else "RealmStatus уже остановлен."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    args = parser.parse_args()
    try:
        print(start() if args.action == "start" else stop())
    except (OSError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
