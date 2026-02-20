"""
Comprehensive tests for the betting-odds-tracker skill.

Covers: RateLimitTracker, ResponseCache, OddsAPI, OddsTracker helper
functions (consensus calculations, line movement, sharp money detection),
formatting helpers, ShippClient, and error-handling paths.
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import requests

# ---------------------------------------------------------------------------
# Import fixup: the parent directory name contains hyphens so we cannot use
# a normal dotted import.  We add the *scripts* package's parent dir to
# sys.path so that ``import scripts.odds_api`` resolves correctly, then we
# also fixup ``scripts`` as a proper top-level package.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR.parent))

from scripts.odds_api import (
    OddsAPI,
    RateLimitTracker,
    ResponseCache,
    CACHE_TTL_SECONDS,
    SPORT_KEYS,
)
from scripts.odds_tracker import (
    OddsTable,
    OddsTracker,
    _format_odds,
    _format_point,
    _consensus_spread,
    _consensus_total,
    _consensus_moneyline,
)
from scripts.shipp_wrapper import ShippClient


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_game(
    game_id="g1",
    home="Team Home",
    away="Team Away",
    commence="2026-02-18T00:00:00Z",
    bookmakers=None,
):
    """Build a normalized game dict matching OddsAPI._normalize_odds output."""
    if bookmakers is None:
        bookmakers = [
            {
                "key": "fanduel",
                "title": "FanDuel",
                "last_update": "2026-02-18T00:00:00Z",
                "markets": {
                    "h2h": {
                        home: {"price": -150, "point": None},
                        away: {"price": 130, "point": None},
                    },
                    "spreads": {
                        home: {"price": -110, "point": -3.5},
                        away: {"price": -110, "point": 3.5},
                    },
                    "totals": {
                        "Over": {"price": -110, "point": 220.5},
                        "Under": {"price": -110, "point": 220.5},
                    },
                },
            },
            {
                "key": "draftkings",
                "title": "DraftKings",
                "last_update": "2026-02-18T00:00:00Z",
                "markets": {
                    "h2h": {
                        home: {"price": -145, "point": None},
                        away: {"price": 125, "point": None},
                    },
                    "spreads": {
                        home: {"price": -110, "point": -4.0},
                        away: {"price": -110, "point": 4.0},
                    },
                    "totals": {
                        "Over": {"price": -110, "point": 221.0},
                        "Under": {"price": -110, "point": 221.0},
                    },
                },
            },
        ]
    return {
        "game_id": game_id,
        "sport": "nba",
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": bookmakers,
    }


def _odds_response(games=None):
    """Wrap games in the standard OddsAPI result dict."""
    if games is None:
        games = [_make_game()]
    return {
        "games": games,
        "count": len(games),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _raw_api_game(
    game_id="g1",
    home="Team Home",
    away="Team Away",
    commence="2026-02-18T00:00:00Z",
):
    """Build a raw API response game (pre-normalization format)."""
    return {
        "id": game_id,
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "fanduel",
                "title": "FanDuel",
                "last_update": "2026-02-18T00:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": -150},
                            {"name": away, "price": 130},
                        ],
                    },
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# 1. RateLimitTracker tests
# ---------------------------------------------------------------------------

class TestRateLimitTracker:
    """Tests for RateLimitTracker."""

    def test_initial_state(self, tmp_path):
        """Fresh tracker starts with 500 remaining and 0 used."""
        tracker = RateLimitTracker(state_path=str(tmp_path / "rl.json"))
        assert tracker.requests_remaining == 500
        assert tracker.requests_used == 0
        assert tracker.can_make_request() is True

    def test_update_from_headers(self, tmp_path):
        """Headers from API response update the counters and persist."""
        path = str(tmp_path / "rl.json")
        tracker = RateLimitTracker(state_path=path)
        tracker.update_from_headers({
            "x-requests-remaining": "480",
            "x-requests-used": "20",
        })
        assert tracker.requests_remaining == 480
        assert tracker.requests_used == 20

        # Reload from disk
        tracker2 = RateLimitTracker(state_path=path)
        assert tracker2.requests_remaining == 480
        assert tracker2.requests_used == 20

    def test_can_make_request_when_exhausted(self, tmp_path):
        """Returns False when quota is zero."""
        tracker = RateLimitTracker(state_path=str(tmp_path / "rl.json"))
        tracker.requests_remaining = 0
        assert tracker.can_make_request() is False

    def test_status_dict(self, tmp_path):
        """status() returns a dict with expected keys."""
        tracker = RateLimitTracker(state_path=str(tmp_path / "rl.json"))
        status = tracker.status()
        assert "requests_used" in status
        assert "requests_remaining" in status
        assert "last_reset" in status
        assert "last_request" in status

    def test_corrupted_state_file_resets(self, tmp_path):
        """Corrupted JSON on disk triggers a clean reset."""
        path = tmp_path / "rl.json"
        path.write_text("NOT-JSON!!!")
        tracker = RateLimitTracker(state_path=str(path))
        assert tracker.requests_remaining == 500
        assert tracker.requests_used == 0


# ---------------------------------------------------------------------------
# 2. ResponseCache tests
# ---------------------------------------------------------------------------

class TestResponseCache:
    """Tests for ResponseCache."""

    def test_cache_miss(self, tmp_path):
        """Returns None on cache miss."""
        cache = ResponseCache(cache_dir=str(tmp_path), ttl=900)
        assert cache.get("nba", ["h2h"]) is None

    def test_cache_set_and_get(self, tmp_path):
        """Data round-trips through set/get."""
        cache = ResponseCache(cache_dir=str(tmp_path), ttl=900)
        data = {"games": [{"id": 1}]}
        cache.set("nba", ["h2h", "spreads"], data)
        result = cache.get("nba", ["h2h", "spreads"])
        assert result == data

    def test_cache_expired(self, tmp_path):
        """Returns None when TTL has elapsed."""
        cache = ResponseCache(cache_dir=str(tmp_path), ttl=1)  # 1 second TTL
        data = {"games": []}
        cache.set("nba", ["h2h"], data)

        # Manually back-date the cached_at timestamp
        key = cache._cache_key("nba", ["h2h"])
        path = cache._cache_path(key)
        with open(path, "r") as f:
            cached = json.load(f)
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        cached["cached_at"] = old_time
        with open(path, "w") as f:
            json.dump(cached, f)

        assert cache.get("nba", ["h2h"]) is None

    def test_cache_key_sorted_markets(self, tmp_path):
        """Markets are sorted so order doesn't matter for cache key."""
        cache = ResponseCache(cache_dir=str(tmp_path))
        k1 = cache._cache_key("nba", ["h2h", "spreads"])
        k2 = cache._cache_key("nba", ["spreads", "h2h"])
        assert k1 == k2


# ---------------------------------------------------------------------------
# 3. OddsAPI tests
# ---------------------------------------------------------------------------

class TestOddsAPI:
    """Tests for OddsAPI client."""

    def _make_api(self, tmp_path):
        """Create an OddsAPI with mocked dependencies."""
        api = OddsAPI.__new__(OddsAPI)
        api.api_key = "test-key"
        api.session = MagicMock()
        api.rate_limit = RateLimitTracker(state_path=str(tmp_path / "rl.json"))
        api.cache = ResponseCache(cache_dir=str(tmp_path / "cache"), ttl=900)
        return api

    def test_missing_api_key_raises(self):
        """OddsAPI raises ValueError when no key is provided."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ODDS_API_KEY", None)
            with pytest.raises(ValueError, match="ODDS_API_KEY is required"):
                OddsAPI(api_key=None)

    def test_get_odds_returns_normalized_data(self, tmp_path):
        """get_odds normalizes raw API response into internal format."""
        api = self._make_api(tmp_path)

        mock_response = MagicMock()
        mock_response.json.return_value = [_raw_api_game()]
        mock_response.headers = {
            "x-requests-remaining": "499",
            "x-requests-used": "1",
        }
        mock_response.raise_for_status = MagicMock()
        api.session.get.return_value = mock_response

        result = api.get_odds("nba", use_cache=False)
        assert "games" in result
        assert result["count"] == 1
        game = result["games"][0]
        assert game["game_id"] == "g1"
        assert game["home_team"] == "Team Home"
        assert len(game["bookmakers"]) == 1

    def test_get_odds_uses_cache(self, tmp_path):
        """Cached response is returned without hitting the network."""
        api = self._make_api(tmp_path)
        cached_data = _odds_response()
        api.cache.set("basketball_nba", ["h2h", "spreads", "totals"], cached_data)

        result = api.get_odds("nba", use_cache=True)
        assert result == cached_data
        api.session.get.assert_not_called()

    def test_get_odds_rate_limit_exhausted(self, tmp_path):
        """RuntimeError raised when quota is zero."""
        api = self._make_api(tmp_path)
        api.rate_limit.requests_remaining = 0

        with pytest.raises(RuntimeError, match="quota exhausted"):
            api.get_odds("nba", use_cache=False)

    def test_get_odds_401_raises_value_error(self, tmp_path):
        """401 response is translated to ValueError about invalid key."""
        api = self._make_api(tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = {"x-requests-remaining": "499", "x-requests-used": "1"}
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        api.session.get.return_value = mock_response

        with pytest.raises(ValueError, match="Invalid Odds API key"):
            api.get_odds("nba", use_cache=False)

    def test_get_odds_422_raises_value_error(self, tmp_path):
        """422 response is translated to ValueError about invalid sport key."""
        api = self._make_api(tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.headers = {"x-requests-remaining": "499", "x-requests-used": "1"}
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        api.session.get.return_value = mock_response

        with pytest.raises(ValueError, match="Invalid sport key"):
            api.get_odds("nba", use_cache=False)

    def test_get_odds_connection_error(self, tmp_path):
        """Network failure raises RuntimeError."""
        api = self._make_api(tmp_path)
        api.session.get.side_effect = requests.exceptions.ConnectionError("dns fail")

        with pytest.raises(RuntimeError, match="Odds API request failed"):
            api.get_odds("nba", use_cache=False)

    def test_resolve_sport_key_known(self, tmp_path):
        """Known friendly names map to API sport keys."""
        api = self._make_api(tmp_path)
        assert api._resolve_sport_key("nba") == "basketball_nba"
        assert api._resolve_sport_key("mlb") == "baseball_mlb"

    def test_resolve_sport_key_passthrough(self, tmp_path):
        """Unknown keys pass through unchanged."""
        api = self._make_api(tmp_path)
        assert api._resolve_sport_key("basketball_nba") == "basketball_nba"

    def test_get_scores(self, tmp_path):
        """get_scores returns normalized score data."""
        api = self._make_api(tmp_path)

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "id": "g1",
                "home_team": "Team Home",
                "away_team": "Team Away",
                "commence_time": "2026-02-18T00:00:00Z",
                "completed": True,
                "scores": [
                    {"name": "Team Home", "score": "110"},
                    {"name": "Team Away", "score": "105"},
                ],
            }
        ]
        mock_response.headers = {
            "x-requests-remaining": "498",
            "x-requests-used": "2",
        }
        mock_response.raise_for_status = MagicMock()
        api.session.get.return_value = mock_response

        result = api.get_scores("nba")
        assert result["count"] == 1
        assert result["scores"][0]["completed"] is True


# ---------------------------------------------------------------------------
# 4. Consensus helper function tests
# ---------------------------------------------------------------------------

class TestConsensusHelpers:
    """Tests for _consensus_spread, _consensus_total, _consensus_moneyline."""

    def test_consensus_spread_multi_bookmaker(self):
        """Average home spread across two books."""
        game = _make_game()
        spread = _consensus_spread(game)
        # FanDuel: -3.5, DraftKings: -4.0 => avg -3.75 => round to -3.8
        assert spread == -3.8

    def test_consensus_spread_no_data(self):
        """Returns None when no bookmakers have spread data."""
        game = _make_game(bookmakers=[])
        assert _consensus_spread(game) is None

    def test_consensus_total_multi_bookmaker(self):
        """Average Over total across two books."""
        game = _make_game()
        total = _consensus_total(game)
        # FanDuel: 220.5, DraftKings: 221.0 => avg 220.75 => round to 220.8
        assert total == 220.8

    def test_consensus_total_no_data(self):
        """Returns None when no bookmakers have total data."""
        game = _make_game(bookmakers=[])
        assert _consensus_total(game) is None

    def test_consensus_moneyline(self):
        """Average moneyline across books."""
        game = _make_game()
        ml = _consensus_moneyline(game)
        assert ml is not None
        # home: avg(-150, -145) = -147.5 => round = -148
        # away: avg(130, 125) = 127.5 => round = 128
        assert ml["home"] == -148
        assert ml["away"] == 128

    def test_consensus_moneyline_no_data(self):
        """Returns None when there is no h2h data."""
        game = _make_game(bookmakers=[])
        assert _consensus_moneyline(game) is None

    def test_consensus_spread_single_bookmaker(self):
        """With one book, consensus equals that book's value."""
        bms = [
            {
                "key": "fanduel",
                "title": "FanDuel",
                "last_update": "",
                "markets": {
                    "spreads": {
                        "Team Home": {"price": -110, "point": -5.0},
                    },
                },
            },
        ]
        game = _make_game(bookmakers=bms)
        assert _consensus_spread(game) == -5.0


# ---------------------------------------------------------------------------
# 5. Formatting helper tests
# ---------------------------------------------------------------------------

class TestFormatHelpers:
    """Tests for _format_odds and _format_point."""

    def test_format_odds_positive(self):
        assert _format_odds(130) == "+130"

    def test_format_odds_negative(self):
        assert _format_odds(-150) == "-150"

    def test_format_odds_none(self):
        assert _format_odds(None) == "-"

    def test_format_odds_empty_string(self):
        assert _format_odds("") == "-"

    def test_format_point_positive(self):
        assert _format_point(3.5) == "+3.5"

    def test_format_point_negative(self):
        assert _format_point(-3.5) == "-3.5"

    def test_format_point_none(self):
        assert _format_point(None) == "-"


# ---------------------------------------------------------------------------
# 6. OddsTable rendering test
# ---------------------------------------------------------------------------

class TestOddsTable:
    """Tests for OddsTable.table() output."""

    def test_table_contains_team_names(self):
        """The table string includes game team names."""
        data = _odds_response()
        data["sport"] = "nba"
        data["rate_limit"] = {"requests_remaining": 495}
        table = OddsTable(data)
        output = table.table()
        assert "Team Away" in output
        assert "Team Home" in output
        assert "NBA" in output.upper()

    def test_table_no_games(self):
        """An empty games list produces header-only output."""
        data = {"games": [], "sport": "nba", "rate_limit": {"requests_remaining": 500}}
        table = OddsTable(data)
        output = table.table()
        assert "NBA" in output.upper()
        assert "500" in output


# ---------------------------------------------------------------------------
# 7. OddsTracker -- line movement & sharp money
# ---------------------------------------------------------------------------

class TestOddsTrackerLineMovement:
    """Tests for OddsTracker.get_line_movement and sharp_money_alerts."""

    def _build_tracker(self, tmp_path):
        """Create an OddsTracker with fully mocked APIs."""
        tracker = OddsTracker.__new__(OddsTracker)
        tracker.odds_api = OddsAPI.__new__(OddsAPI)
        tracker.odds_api.api_key = "test"
        tracker.odds_api.session = MagicMock()
        tracker.odds_api.rate_limit = RateLimitTracker(
            state_path=str(tmp_path / "rl.json")
        )
        tracker.odds_api.cache = ResponseCache(
            cache_dir=str(tmp_path / "cache"), ttl=900
        )
        tracker.shipp = ShippClient.__new__(ShippClient)
        tracker.shipp.api_key = "test"
        tracker.shipp.session = MagicMock()
        tracker.shipp._connections = {}
        tracker.snapshots_dir = tmp_path / "snapshots"
        tracker.snapshots_dir.mkdir(parents=True, exist_ok=True)
        return tracker

    def test_line_movement_with_opening_snapshot(self, tmp_path):
        """Movement is computed between opening snapshot and current odds."""
        tracker = self._build_tracker(tmp_path)

        # Create an opening snapshot with different spreads
        opening_game = _make_game()
        # Set the opening spread to -2.0 (both bookmakers)
        for bm in opening_game["bookmakers"]:
            bm["markets"]["spreads"]["Team Home"]["point"] = -2.0
            bm["markets"]["spreads"]["Team Away"]["point"] = 2.0
            bm["markets"]["totals"]["Over"]["point"] = 218.0
            bm["markets"]["totals"]["Under"]["point"] = 218.0

        snapshot = {
            "sport": "nba",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "games": [opening_game],
        }
        snapshot_path = tracker.snapshots_dir / "nba_20260218_000000.json"
        with open(snapshot_path, "w") as f:
            json.dump(snapshot, f)

        # Current odds (spreads at -3.5/-4.0 as in _make_game default)
        current_data = _odds_response()
        tracker.odds_api.cache.set("basketball_nba", ["h2h", "spreads", "totals"], current_data)

        movements = tracker.get_line_movement("nba")
        assert len(movements) == 1
        m = movements[0]
        # open_spread = -2.0, current_spread ~ -3.8
        assert m["open_spread"] == -2.0
        assert m["current_spread"] is not None
        assert m["spread_delta"] is not None
        # Delta should be negative (line moved further toward home favorite)
        assert m["spread_delta"] < 0
        # abs(delta) >= 1.0 triggers reverse_movement flag
        assert m["reverse_movement"] is True

    def test_line_movement_no_opening(self, tmp_path):
        """No opening snapshot means no movement data."""
        tracker = self._build_tracker(tmp_path)
        current_data = _odds_response()
        tracker.odds_api.cache.set("basketball_nba", ["h2h", "spreads", "totals"], current_data)
        movements = tracker.get_line_movement("nba")
        assert movements == []

    def test_sharp_money_significant_spread(self, tmp_path):
        """Spread movement >= 1.5 triggers a significant_spread_movement alert."""
        tracker = self._build_tracker(tmp_path)

        opening_game = _make_game()
        for bm in opening_game["bookmakers"]:
            bm["markets"]["spreads"]["Team Home"]["point"] = -1.0

        snapshot = {
            "sport": "nba",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "games": [opening_game],
        }
        with open(tracker.snapshots_dir / "nba_20260218_000000.json", "w") as f:
            json.dump(snapshot, f)

        current_data = _odds_response()
        tracker.odds_api.cache.set("basketball_nba", ["h2h", "spreads", "totals"], current_data)

        alerts = tracker.sharp_money_alerts("nba")
        signal_types = [a["signal"] for a in alerts]
        assert "significant_spread_movement" in signal_types

    def test_sharp_money_total_movement(self, tmp_path):
        """Total movement >= 2.0 triggers a significant_total_movement alert."""
        tracker = self._build_tracker(tmp_path)

        opening_game = _make_game()
        for bm in opening_game["bookmakers"]:
            bm["markets"]["totals"]["Over"]["point"] = 215.0

        snapshot = {
            "sport": "nba",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "games": [opening_game],
        }
        with open(tracker.snapshots_dir / "nba_20260218_000000.json", "w") as f:
            json.dump(snapshot, f)

        current_data = _odds_response()
        tracker.odds_api.cache.set("basketball_nba", ["h2h", "spreads", "totals"], current_data)

        alerts = tracker.sharp_money_alerts("nba")
        signal_types = [a["signal"] for a in alerts]
        assert "significant_total_movement" in signal_types

    def test_sharp_money_no_alerts_small_movement(self, tmp_path):
        """Small movements produce no alerts."""
        tracker = self._build_tracker(tmp_path)

        # Opening spread very close to current
        opening_game = _make_game()
        for bm in opening_game["bookmakers"]:
            bm["markets"]["spreads"]["Team Home"]["point"] = -3.5

        snapshot = {
            "sport": "nba",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "games": [opening_game],
        }
        with open(tracker.snapshots_dir / "nba_20260218_000000.json", "w") as f:
            json.dump(snapshot, f)

        current_data = _odds_response()
        # Ensure current spread is almost the same as opening
        for g in current_data["games"]:
            for bm in g["bookmakers"]:
                bm["markets"]["spreads"]["Team Home"]["point"] = -3.5
        tracker.odds_api.cache.set("basketball_nba", ["h2h", "spreads", "totals"], current_data)

        alerts = tracker.sharp_money_alerts("nba")
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# 8. OddsTracker best_odds
# ---------------------------------------------------------------------------

class TestBestOdds:
    """Tests for OddsTracker.best_odds."""

    def test_best_odds_picks_highest(self, tmp_path):
        """best_odds returns the highest price across bookmakers."""
        tracker = OddsTracker.__new__(OddsTracker)
        tracker.odds_api = OddsAPI.__new__(OddsAPI)
        tracker.odds_api.api_key = "test"
        tracker.odds_api.session = MagicMock()
        tracker.odds_api.rate_limit = RateLimitTracker(
            state_path=str(tmp_path / "rl.json")
        )
        tracker.odds_api.cache = ResponseCache(
            cache_dir=str(tmp_path / "cache"), ttl=900
        )
        tracker.shipp = ShippClient.__new__(ShippClient)
        tracker.shipp.api_key = "test"
        tracker.shipp.session = MagicMock()
        tracker.shipp._connections = {}
        tracker.snapshots_dir = tmp_path / "snapshots"
        tracker.snapshots_dir.mkdir(parents=True, exist_ok=True)

        data = _odds_response()
        tracker.odds_api.cache.set("basketball_nba", ["h2h"], data)
        best = tracker.best_odds("nba", market="h2h")
        assert len(best) == 1
        # FanDuel away=130, DraftKings away=125 => best away = 130 (FanDuel)
        assert best[0]["best_away_odds"] == 130
        assert best[0]["best_away_book"] == "FanDuel"
        # FanDuel home=-150, DraftKings home=-145 => best home = -145 (DraftKings)
        assert best[0]["best_home_odds"] == -145
        assert best[0]["best_home_book"] == "DraftKings"


# ---------------------------------------------------------------------------
# 9. ShippClient tests
# ---------------------------------------------------------------------------

class TestShippClient:
    """Tests for the ShippClient wrapper."""

    def test_missing_api_key_raises(self):
        """ShippClient raises ValueError when no key is provided."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SHIPP_API_KEY", None)
            with pytest.raises(ValueError, match="SHIPP_API_KEY is required"):
                ShippClient(api_key=None)

    def test_get_game_context_returns_list(self):
        """get_game_context returns a list even when API fails."""
        client = ShippClient.__new__(ShippClient)
        client.api_key = "test"
        client.session = MagicMock()
        client._connections = {}

        # Make _request always raise
        client.session.request.side_effect = requests.exceptions.ConnectionError("nope")

        result = client.get_game_context("nba")
        # Should gracefully return [] on failure
        assert result == []

    def test_rate_limited_request_retries(self):
        """429 response triggers a retry (with sleep mocked out)."""
        client = ShippClient.__new__(ShippClient)
        client.api_key = "test"
        client.session = MagicMock()
        client._connections = {}

        # First call: 429, second call: 200
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"games": []}
        resp_200.raise_for_status = MagicMock()

        client.session.request.side_effect = [resp_429, resp_200]

        with mock.patch("scripts.shipp_wrapper.time.sleep"):
            result = client._request("GET", "/sports/nba/schedule")
        assert result == {"games": []}


# ---------------------------------------------------------------------------
# 10. OddsAPI.get_available_sports
# ---------------------------------------------------------------------------

class TestGetAvailableSports:
    """Tests for OddsAPI.get_available_sports."""

    def test_returns_list(self, tmp_path):
        """get_available_sports returns whatever the API returns."""
        api = OddsAPI.__new__(OddsAPI)
        api.api_key = "test"
        api.session = MagicMock()
        api.rate_limit = RateLimitTracker(state_path=str(tmp_path / "rl.json"))
        api.cache = ResponseCache(cache_dir=str(tmp_path / "cache"))

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"key": "basketball_nba", "title": "NBA"},
            {"key": "baseball_mlb", "title": "MLB"},
        ]
        mock_resp.headers = {"x-requests-remaining": "499", "x-requests-used": "1"}
        mock_resp.raise_for_status = MagicMock()
        api.session.get.return_value = mock_resp

        result = api.get_available_sports()
        assert len(result) == 2
        assert result[0]["key"] == "basketball_nba"

    def test_rate_limit_blocks_sports_call(self, tmp_path):
        """get_available_sports raises when quota exhausted."""
        api = OddsAPI.__new__(OddsAPI)
        api.api_key = "test"
        api.session = MagicMock()
        api.rate_limit = RateLimitTracker(state_path=str(tmp_path / "rl.json"))
        api.rate_limit.requests_remaining = 0
        api.cache = ResponseCache(cache_dir=str(tmp_path / "cache"))

        with pytest.raises(RuntimeError, match="quota exhausted"):
            api.get_available_sports()
