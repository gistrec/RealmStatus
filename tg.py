import json
import os

from datetime import datetime, timezone, timedelta
from typing import Dict, List, TypedDict, cast

from telegram import Bot
from telegram.error import BadRequest

from db import get_setting, remove_setting, set_setting


# === CONFIG ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MESSAGE_ID_KEY = "realm_status_message_id"
PLAYER_SESSIONS_KEY = "realm_player_sessions"
PLAYTIME_LOG_KEY = "realm_playtime_log"

SESSION_GRACE_PERIOD = timedelta(minutes=10)
WEEK_SECONDS = 7 * 24 * 3600

MSK = timezone(timedelta(hours=3))
MINECRAFT_SOURCE = "minecraft"
STARDEW_SOURCE = "stardew"

class PlayerSession(TypedDict):
    started_at: int
    last_seen: int


def _as_object_dict(value: object) -> dict[object, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[object, object], value)


def _load_player_sessions() -> Dict[str, PlayerSession]:
    raw = get_setting(PLAYER_SESSIONS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    data_dict = _as_object_dict(data)
    if data_dict is None:
        return {}
    sessions: Dict[str, PlayerSession] = {}
    for name, session in data_dict.items():
        if not isinstance(name, str):
            continue
        if isinstance(session, int):
            sessions[name] = {"started_at": session, "last_seen": session}
            continue
        session_dict = _as_object_dict(session)
        if session_dict is not None:
            started_at = session_dict.get("started_at")
            last_seen = session_dict.get("last_seen")
            if isinstance(started_at, int) and isinstance(last_seen, int):
                sessions[name] = {"started_at": started_at, "last_seen": last_seen}
    return sessions


def _save_player_sessions(sessions: Dict[str, PlayerSession]) -> None:
    if not sessions:
        remove_setting(PLAYER_SESSIONS_KEY)
        return
    set_setting(PLAYER_SESSIONS_KEY, json.dumps(sessions))


# Playtime log: {player: [[start_ts, duration_secs], ...]}
def _load_playtime_log() -> Dict[str, List[List[int]]]:
    raw = get_setting(PLAYTIME_LOG_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    data_dict = _as_object_dict(data)
    if data_dict is None:
        return {}
    parsed: Dict[str, List[List[int]]] = {}
    for player, entries in data_dict.items():
        if not isinstance(player, str) or not isinstance(entries, list):
            continue
        typed_entries: List[List[int]] = []
        for entry in cast(list[object], entries):
            if not isinstance(entry, list):
                continue
            entry_list = cast(list[object], entry)
            if (
                len(entry_list) == 2
                and isinstance(entry_list[0], int)
                and isinstance(entry_list[1], int)
            ):
                typed_entries.append([entry_list[0], entry_list[1]])
        if typed_entries:
            parsed[player] = typed_entries
    return parsed


def _save_playtime_log(log: Dict[str, List[List[int]]], now_ts: int) -> None:
    cutoff = now_ts - WEEK_SECONDS
    pruned = {
        player: [[s, d] for s, d in entries if s >= cutoff]
        for player, entries in log.items()
    }
    pruned = {p: e for p, e in pruned.items() if e}
    if not pruned:
        remove_setting(PLAYTIME_LOG_KEY)
    else:
        set_setting(PLAYTIME_LOG_KEY, json.dumps(pruned))


def _record_session(log: Dict[str, List[List[int]]], player: str, start_ts: int, end_ts: int) -> None:
    duration = max(end_ts - start_ts, 0)
    if duration == 0:
        return
    log.setdefault(player, []).append([start_ts, duration])


def _player_key(source: str, player_name: str) -> str:
    return f"{source}:{player_name}"


def _player_name_from_key(player_key: str) -> str:
    return player_key.split(":", 1)[1] if ":" in player_key else player_key


def _player_game_label(player_key: str) -> str:
    if player_key.startswith(f"{MINECRAFT_SOURCE}:"):
        return "Minecraft"
    if player_key.startswith(f"{STARDEW_SOURCE}:"):
        return "Stardew Valley"
    return "Unknown"


def _format_live_section(title: str, player_keys: List[str], sessions: Dict[str, PlayerSession], now_utc: datetime) -> str:
    if not player_keys:
        return f"{title}: 0\n— никого нет —"

    lines = "\n".join(
        f"• {_player_name_from_key(player_key)} "
        f"{_format_duration(datetime.fromtimestamp(sessions[player_key]['started_at'], timezone.utc), now_utc)}"
        for player_key in player_keys
    )
    return f"{title}: {len(player_keys)}\n{lines}"


def _format_playtime(total_seconds: int) -> str:
    total_minutes = total_seconds // 60
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours == 0:
        return f"{minutes} мин"
    return f"{hours}ч {minutes} мин"


def _format_duration(started_at: datetime, now: datetime) -> str:
    total_minutes = int((now - started_at).total_seconds() // 60)
    total_minutes = max(total_minutes, 0)

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours == 0 and minutes == 0:
        return f"(Играет недавно)"

    if hours == 0:
        return f"(Играет {minutes} мин)"

    return f"(Играет {hours}ч {minutes} мин)"


def _format_message(
    minecraft_players: List[str],
    stardew_players: List[str],
    sessions: Dict[str, PlayerSession],
    playtime_log: Dict[str, List[List[int]]],
    now_ts: int,
) -> str:
    now_msk = datetime.now(MSK).strftime("%H:%M")
    now_utc = datetime.fromtimestamp(now_ts, timezone.utc)
    minecraft_keys = [_player_key(MINECRAFT_SOURCE, name) for name in minecraft_players]
    stardew_keys = [_player_key(STARDEW_SOURCE, name) for name in stardew_players]
    all_online_keys = minecraft_keys + stardew_keys
    online = len(all_online_keys)
    minecraft_section = _format_live_section("🎮 *Minecraft*", minecraft_keys, sessions, now_utc)
    stardew_section = _format_live_section("🌾 *Stardew Valley*", stardew_keys, sessions, now_utc)

    # Weekly stats: all players with playtime in the last 7 days
    cutoff = now_ts - WEEK_SECONDS
    weekly: Dict[str, int] = {}
    for player, entries in playtime_log.items():
        total = sum(d for s, d in entries if s >= cutoff)
        if total > 0:
            weekly[player] = total
    for player_key, session in sessions.items():
        weekly[player_key] = weekly.get(player_key, 0) + max(now_ts - session["started_at"], 0)

    if weekly:
        weekly_lines = "\n".join(
            f"• {_player_name_from_key(player_key)} ({_player_game_label(player_key)}): {_format_playtime(secs)}"
            for player_key, secs in sorted(weekly.items(), key=lambda x: -x[1])
        )
        weekly_section = f"\n📊 *За неделю:*\n{weekly_lines}\n"
    else:
        weekly_section = ""

    return (
        f"👥 *Онлайн:* {online}\n"
        f"\n"
        f"{minecraft_section}\n"
        f"\n"
        f"{stardew_section}\n"
        f"{weekly_section}"
        f"\n"
        f"🕒 _Обновлено: {now_msk} (МСК)_"
    )


async def update_status(minecraft_players: List[str], stardew_players: List[str]) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env variable")
    if not CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_CHAT_ID env variable")

    bot = Bot(token=BOT_TOKEN)
    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())

    sessions = _load_player_sessions()
    playtime_log = _load_playtime_log()

    current_players = (
        [_player_key(MINECRAFT_SOURCE, name) for name in minecraft_players]
        + [_player_key(STARDEW_SOURCE, name) for name in stardew_players]
    )

    # Update last_seen for current players, create new sessions for newcomers
    for player_key in current_players:
        if player_key in sessions:
            sessions[player_key]["last_seen"] = now_ts
        else:
            sessions[player_key] = {"started_at": now_ts, "last_seen": now_ts}

    # Record completed sessions for players whose grace period has expired
    grace_cutoff = int((now_utc - SESSION_GRACE_PERIOD).timestamp())
    for name, session in sessions.items():
        if session["last_seen"] < grace_cutoff:
            _record_session(playtime_log, name, session["started_at"], session["last_seen"])

    # Prune expired sessions
    sessions = {
        name: session
        for name, session in sessions.items()
        if session["last_seen"] >= grace_cutoff
    }

    _save_player_sessions(sessions)
    _save_playtime_log(playtime_log, now_ts)
    text = _format_message(minecraft_players, stardew_players, sessions, playtime_log, now_ts)

    message_id = get_setting(MESSAGE_ID_KEY)

    try:
        if message_id:
            # Пытаемся отредактировать сообщение
            await bot.edit_message_text(
                chat_id=CHAT_ID,
                message_id=int(message_id),
                text=text,
                parse_mode="Markdown",
            )
        else:
            raise ValueError("No message_id")

    except BadRequest as e:
        if "message is too old" in str(e) or "can't be edited" in str(e):
            # Если сообщение старое или его нет — отправляем новое
            msg = await bot.send_message(
                chat_id=CHAT_ID,
                text=text,
                parse_mode="Markdown",
                disable_notification=True,
            )

            # Закрепляем без звука
            await bot.pin_chat_message(
                chat_id=CHAT_ID,
                message_id=msg.message_id,
                disable_notification=True,
            )

            set_setting(MESSAGE_ID_KEY, str(msg.message_id))
