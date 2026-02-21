"""
Thin wrapper around the Shipp.ai API for live game score context.

Provides live scores and game status so the odds tracker can correlate
line movements with on-field action.
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SHIPP_BASE_URL = "https://api.shipp.ai/api/v1"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BACKOFF = 2.0


class ShippClient:
    """Client for Shipp.ai live score and schedule data."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("SHIPP_API_KEY")
        if not self.api_key:
            raise ValueError(
                "SHIPP_API_KEY is required. Set it as an environment variable "
                "or pass it to ShippClient(api_key='...'). "
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "betting-odds-tracker/1.0",
        })
        self._connections = {}

    def _url(self, endpoint: str) -> str:
        """Build URL with api_key query parameter."""
        sep = "&" if "?" in endpoint else "?"
        return f"{SHIPP_BASE_URL}{endpoint}{sep}api_key={self.api_key}"

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an API request with retry and rate-limit handling."""
        url = self._url(endpoint)
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning("Rate limited, waiting %d seconds", retry_after)
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                last_error = f"Timeout after {DEFAULT_TIMEOUT}s"
                logger.warning("Attempt %d: %s", attempt + 1, last_error)
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
                logger.warning("Attempt %d: %s", attempt + 1, last_error)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code < 500:
                    raise
                last_error = f"HTTP error: {e}"
                logger.warning("Attempt %d: %s", attempt + 1, last_error)

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))

        raise RuntimeError(
            f"Shipp API failed after {MAX_RETRIES + 1} attempts: {last_error}"
        )

    def get_schedule(self, sport: str, date: Optional[str] = None) -> dict:
        """
        Get the game schedule for a sport.

        Args:
            sport: One of 'nba', 'mlb', 'soccer'
            date: YYYY-MM-DD format. Defaults to today.

        Returns:
            dict with 'games' list.
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._request("GET", f"/sports/{sport}/schedule", params={"date": date})

    def get_live_scores(self, sport: str) -> list:
        """
        Get live scores for currently active games.

        Creates a Shipp connection and polls for current state.

        Returns:
            List of live game event dicts.
        """
        try:
            # Reuse existing connection if available
            conn_id = self._connections.get(sport)
            if not conn_id:
                filter_map = {
                    "nba": "Track all NBA games today with live scores and game status",
                    "mlb": "Track all MLB games today with live scores and game status",
                    "soccer": "Track all soccer matches today with live scores and game status",
                }
                conn = self._request("POST", "/connections/create", json={
                    "filter_instructions": filter_map.get(sport, f"Track all {sport} games today with live scores"),
                })
                conn_id = conn.get("connection_id")
                if conn_id:
                    self._connections[sport] = conn_id

            if not conn_id:
                logger.error("No connection_id for %s", sport)
                return []

            result = self._request("POST", f"/connections/{conn_id}", json={"limit": 50})
            return result.get("data", result.get("events", []))

        except Exception as e:
            logger.error("Failed to get live scores for %s: %s", sport, e)
            # Clear stale connection
            self._connections.pop(sport, None)
            return []

    def get_game_context(self, sport: str) -> list:
        """
        Get today's games with current scores for odds context.

        Returns:
            List of game dicts with: game_id, home_team, away_team,
            start_time, status, score.
        """
        try:
            schedule = self.get_schedule(sport)
            games = schedule.get("games", [])

            # Enrich with live scores for active games
            live_events = self.get_live_scores(sport)
            score_map = {}
            for event in live_events:
                gid = event.get("game_id")
                if gid:
                    score_map[gid] = {
                        "home_score": event.get("home_score"),
                        "away_score": event.get("away_score"),
                        "period": event.get("period"),
                        "clock": event.get("clock"),
                    }

            for game in games:
                gid = game.get("game_id")
                if gid in score_map:
                    game["live_score"] = score_map[gid]

            return games

        except Exception as e:
            logger.error("Failed to get game context for %s: %s", sport, e)
            return []
