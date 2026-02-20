"""
The Odds API integration for fetching betting odds across sportsbooks.

Handles API communication, rate limit tracking, response caching, and
data normalization for The Odds API (https://the-odds-api.com).

Free tier: 500 requests/month. This module is designed to be efficient
with your quota through aggressive caching and batched requests.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
DEFAULT_TIMEOUT = 15

# Sport keys for The Odds API
SPORT_KEYS = {
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "soccer_epl": "soccer_epl",
    "soccer_la_liga": "soccer_spain_la_liga",
    "soccer_ucl": "soccer_uefa_champions_league",
    "soccer_mls": "soccer_usa_mls",
    "soccer": "soccer_epl",  # default soccer = EPL
}

# Available markets
MARKETS = ["h2h", "spreads", "totals"]

# Default bookmakers to include (US-focused)
DEFAULT_REGIONS = "us"

# Cache settings
CACHE_TTL_SECONDS = 900  # 15 minutes
CACHE_DIR = os.path.expanduser("~/.betting_odds_tracker/cache")


class RateLimitTracker:
    """Tracks The Odds API rate limit usage."""

    def __init__(self, state_path: Optional[str] = None):
        self.state_path = Path(
            state_path or os.path.expanduser("~/.betting_odds_tracker/rate_limit.json")
        )
        self._load()

    def _load(self):
        """Load rate limit state from disk."""
        if self.state_path.exists():
            try:
                with open(self.state_path, "r") as f:
                    data = json.load(f)
                self.requests_used = data.get("requests_used", 0)
                self.requests_remaining = data.get("requests_remaining", 500)
                self.last_reset = data.get("last_reset", "")
                self.last_request = data.get("last_request", "")
            except (json.JSONDecodeError, IOError):
                self._reset()
        else:
            self._reset()

    def _reset(self):
        """Reset rate limit counters."""
        self.requests_used = 0
        self.requests_remaining = 500
        self.last_reset = datetime.now(timezone.utc).isoformat()
        self.last_request = ""

    def _save(self):
        """Persist rate limit state."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump({
                "requests_used": self.requests_used,
                "requests_remaining": self.requests_remaining,
                "last_reset": self.last_reset,
                "last_request": self.last_request,
            }, f, indent=2)

    def update_from_headers(self, headers: dict):
        """Update counters from API response headers."""
        remaining = headers.get("x-requests-remaining")
        used = headers.get("x-requests-used")

        if remaining is not None:
            self.requests_remaining = int(remaining)
        if used is not None:
            self.requests_used = int(used)

        self.last_request = datetime.now(timezone.utc).isoformat()
        self._save()

    def can_make_request(self) -> bool:
        """Check if we have remaining quota."""
        return self.requests_remaining > 0

    def status(self) -> dict:
        """Return current rate limit status."""
        return {
            "requests_used": self.requests_used,
            "requests_remaining": self.requests_remaining,
            "last_reset": self.last_reset,
            "last_request": self.last_request,
        }


class ResponseCache:
    """Simple file-based cache for API responses."""

    def __init__(self, cache_dir: Optional[str] = None, ttl: int = CACHE_TTL_SECONDS):
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.ttl = ttl
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, sport: str, markets: list) -> str:
        """Generate a cache key from request parameters."""
        markets_str = "_".join(sorted(markets))
        return f"{sport}_{markets_str}"

    def _cache_path(self, key: str) -> Path:
        """Get the file path for a cache key."""
        return self.cache_dir / f"{key}.json"

    def get(self, sport: str, markets: list) -> Optional[dict]:
        """
        Retrieve a cached response if it exists and is still fresh.

        Returns:
            Cached response dict, or None if cache miss.
        """
        key = self._cache_key(sport, markets)
        path = self._cache_path(key)

        if not path.exists():
            return None

        try:
            with open(path, "r") as f:
                cached = json.load(f)

            cached_at = cached.get("cached_at", "")
            if cached_at:
                cached_time = datetime.fromisoformat(cached_at)
                age = (datetime.now(timezone.utc) - cached_time).total_seconds()
                if age < self.ttl:
                    logger.debug("Cache hit for %s (age: %.0fs)", key, age)
                    return cached.get("data")
                else:
                    logger.debug("Cache expired for %s (age: %.0fs)", key, age)

        except (json.JSONDecodeError, IOError, ValueError) as e:
            logger.warning("Cache read error for %s: %s", key, e)

        return None

    def set(self, sport: str, markets: list, data: dict):
        """Store a response in the cache."""
        key = self._cache_key(sport, markets)
        path = self._cache_path(key)

        try:
            with open(path, "w") as f:
                json.dump({
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "sport": sport,
                    "markets": markets,
                    "data": data,
                }, f)
        except IOError as e:
            logger.warning("Cache write error for %s: %s", key, e)


class OddsAPI:
    """
    Client for The Odds API.

    Handles authentication, rate limiting, caching, and response
    normalization for betting odds data.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ODDS_API_KEY is required. Set it as an environment variable "
                "or pass it to OddsAPI(api_key='...'). "
                "Get a free key at https://the-odds-api.com"
            )
        self.session = requests.Session()
        self.rate_limit = RateLimitTracker()
        self.cache = ResponseCache()

    def _resolve_sport_key(self, sport: str) -> str:
        """Resolve a friendly sport name to an Odds API sport key."""
        key = SPORT_KEYS.get(sport)
        if key is None:
            # Assume it's already an Odds API sport key
            return sport
        return key

    def get_odds(
        self,
        sport: str,
        markets: Optional[list] = None,
        regions: str = DEFAULT_REGIONS,
        bookmakers: Optional[list] = None,
        use_cache: bool = True,
    ) -> dict:
        """
        Fetch current odds for a sport across sportsbooks.

        Args:
            sport: Sport identifier ('nba', 'mlb', 'soccer_epl', etc.)
            markets: List of markets to fetch. Default: ['h2h', 'spreads', 'totals']
            regions: Region filter. Default: 'us'
            bookmakers: Specific bookmakers to include (optional).
            use_cache: Whether to check cache first. Default: True.

        Returns:
            dict with 'games' list, each containing odds from multiple books.
        """
        if markets is None:
            markets = MARKETS.copy()

        sport_key = self._resolve_sport_key(sport)

        # Check cache
        if use_cache:
            cached = self.cache.get(sport_key, markets)
            if cached is not None:
                return cached

        # Check rate limit
        if not self.rate_limit.can_make_request():
            logger.error(
                "Odds API rate limit exhausted. Used: %d, Remaining: %d",
                self.rate_limit.requests_used,
                self.rate_limit.requests_remaining,
            )
            raise RuntimeError(
                "The Odds API monthly quota exhausted. "
                f"Used {self.rate_limit.requests_used} of 500 requests. "
                "Quota resets on the 1st of next month."
            )

        # Build request
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "american",
        }
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)

        url = f"{ODDS_API_BASE}/sports/{sport_key}/odds/"
        logger.info("Fetching odds for %s (markets: %s)", sport_key, markets)

        try:
            response = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)

            # Update rate limit from headers
            self.rate_limit.update_from_headers(dict(response.headers))

            response.raise_for_status()
            raw_games = response.json()

        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                if e.response.status_code == 401:
                    raise ValueError("Invalid Odds API key. Check ODDS_API_KEY.") from e
                if e.response.status_code == 422:
                    raise ValueError(
                        f"Invalid sport key '{sport_key}' or market. "
                        f"Valid sport keys: {list(SPORT_KEYS.values())}"
                    ) from e
            raise
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Odds API request failed: {e}") from e

        # Normalize response
        result = self._normalize_odds(raw_games, sport)

        # Cache the result
        self.cache.set(sport_key, markets, result)

        return result

    def _normalize_odds(self, raw_games: list, sport: str) -> dict:
        """Normalize The Odds API response into a consistent structure."""
        games = []

        for raw in raw_games:
            game = {
                "game_id": raw.get("id"),
                "sport": sport,
                "home_team": raw.get("home_team"),
                "away_team": raw.get("away_team"),
                "commence_time": raw.get("commence_time"),
                "bookmakers": [],
            }

            for bm in raw.get("bookmakers", []):
                bookmaker = {
                    "key": bm.get("key"),
                    "title": bm.get("title"),
                    "last_update": bm.get("last_update"),
                    "markets": {},
                }

                for market in bm.get("markets", []):
                    market_key = market.get("key")
                    outcomes = {}
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name")
                        outcomes[name] = {
                            "price": outcome.get("price"),
                            "point": outcome.get("point"),
                        }
                    bookmaker["markets"][market_key] = outcomes

                game["bookmakers"].append(bookmaker)

            games.append(game)

        return {
            "games": games,
            "count": len(games),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_scores(self, sport: str, days_from: int = 1) -> dict:
        """
        Fetch completed game scores for settlement verification.

        Args:
            sport: Sport identifier.
            days_from: How many days back to look. Default: 1.

        Returns:
            dict with 'scores' list of completed games.
        """
        sport_key = self._resolve_sport_key(sport)

        if not self.rate_limit.can_make_request():
            raise RuntimeError("Odds API monthly quota exhausted.")

        params = {
            "apiKey": self.api_key,
            "daysFrom": days_from,
        }

        url = f"{ODDS_API_BASE}/sports/{sport_key}/scores/"
        logger.info("Fetching scores for %s", sport_key)

        try:
            response = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            self.rate_limit.update_from_headers(dict(response.headers))
            response.raise_for_status()
            raw_scores = response.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Odds API scores request failed: {e}") from e

        scores = []
        for raw in raw_scores:
            score = {
                "game_id": raw.get("id"),
                "home_team": raw.get("home_team"),
                "away_team": raw.get("away_team"),
                "commence_time": raw.get("commence_time"),
                "completed": raw.get("completed", False),
                "scores": raw.get("scores"),
            }
            scores.append(score)

        return {
            "scores": scores,
            "count": len(scores),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_rate_limit_status(self) -> dict:
        """Return current rate limit usage."""
        return self.rate_limit.status()

    def get_available_sports(self) -> list:
        """Fetch the list of available sports from The Odds API."""
        if not self.rate_limit.can_make_request():
            raise RuntimeError("Odds API monthly quota exhausted.")

        params = {"apiKey": self.api_key}
        url = f"{ODDS_API_BASE}/sports/"

        try:
            response = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            self.rate_limit.update_from_headers(dict(response.headers))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to fetch available sports: {e}") from e
