from __future__ import annotations

import os
from typing import TypedDict, cast

import requests


class StardewStatus(TypedDict):
    playerCount: int
    maxPlayers: int
    steamInviteCode: str
    gogInviteCode: str
    serverVersion: str
    isOnline: bool
    isReady: bool
    lastUpdated: str
    farmName: str
    day: int
    season: str
    year: int
    timeOfDay: int


class StardewHealth(TypedDict):
    status: str
    timestamp: str


class StardewPlayer(TypedDict):
    id: int
    name: str
    isOnline: bool


class StardewPlayersResponse(TypedDict):
    players: list[StardewPlayer]


class StardewValleyService:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        resolved_base_url = (base_url or os.getenv("STARDEW_API_BASE_URL") or "").rstrip("/")
        resolved_api_token = api_token or os.getenv("STARDEW_API_TOKEN")
        if not resolved_base_url:
            raise RuntimeError("Missing STARDEW_API_BASE_URL env variable")
        if not resolved_api_token:
            raise RuntimeError("Missing STARDEW_API_TOKEN env variable")

        self._base_url = resolved_base_url
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {resolved_api_token}"})

    def get_status(self) -> StardewStatus:
        response = self._session.get(f"{self._base_url}/status", timeout=self._timeout_seconds)
        response.raise_for_status()
        return cast(StardewStatus, response.json())

    def get_health(self) -> StardewHealth:
        response = self._session.get(f"{self._base_url}/health", timeout=self._timeout_seconds)
        response.raise_for_status()
        return cast(StardewHealth, response.json())

    def get_players(self) -> StardewPlayersResponse:
        response = self._session.get(f"{self._base_url}/players", timeout=self._timeout_seconds)
        response.raise_for_status()
        return cast(StardewPlayersResponse, response.json())

    def get_online_player_names(self) -> list[str]:
        players_response = self.get_players()
        return [player["name"] for player in players_response["players"] if player["isOnline"]]
