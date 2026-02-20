"""
Betting Odds Tracker — Main orchestrator.

Tracks line movements across sportsbooks, identifies sharp money signals,
and provides cross-book odds comparison for NBA, MLB, and Soccer.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .odds_api import OddsAPI, SPORT_KEYS
from .shipp_wrapper import ShippClient

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = os.path.expanduser("~/.betting_odds_tracker/snapshots")


class OddsTable:
    """Formatted odds comparison table for display."""

    def __init__(self, data: dict):
        self.data = data

    def table(self) -> str:
        """Render a human-readable odds comparison table."""
        lines = []
        sport = self.data.get("sport", "").upper()
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
        lines.append(f"=== {sport} ODDS -- {date_str} ===")
        lines.append("")

        for game in self.data.get("games", []):
            away = game.get("away_team", "Away")
            home = game.get("home_team", "Home")
            commence = game.get("commence_time", "TBD")

            # Format time
            try:
                dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                time_str = dt.strftime("%I:%M %p ET")
            except (ValueError, AttributeError):
                time_str = commence

            lines.append(f"{away} @ {home} -- {time_str}")

            # Header
            lines.append(
                f"  {'Book':<16} {'Moneyline':<20} {'Spread':<20} {'Total':<12}"
            )
            lines.append(f"  {'':<16} {away[:6]:<10}{home[:6]:<10} "
                        f"{away[:6]:<10}{home[:6]:<10} {'O/U':<12}")
            lines.append(f"  {'-' * 68}")

            best_away_ml = None
            best_home_ml = None
            best_away_ml_book = ""
            best_home_ml_book = ""

            for bm in game.get("bookmakers", []):
                title = bm.get("title", "Unknown")[:14]
                markets = bm.get("markets", {})

                # Moneyline
                h2h = markets.get("h2h", {})
                away_ml = h2h.get(away, {}).get("price", "")
                home_ml = h2h.get(home, {}).get("price", "")
                ml_str = f"{_format_odds(away_ml):<10}{_format_odds(home_ml):<10}"

                # Track best moneyline
                if isinstance(away_ml, (int, float)):
                    if best_away_ml is None or away_ml > best_away_ml:
                        best_away_ml = away_ml
                        best_away_ml_book = bm.get("title", "")
                if isinstance(home_ml, (int, float)):
                    if best_home_ml is None or home_ml > best_home_ml:
                        best_home_ml = home_ml
                        best_home_ml_book = bm.get("title", "")

                # Spreads
                spreads = markets.get("spreads", {})
                away_spread = spreads.get(away, {})
                home_spread = spreads.get(home, {})
                away_pt = away_spread.get("point", "")
                home_pt = home_spread.get("point", "")
                spread_str = f"{_format_point(away_pt):<10}{_format_point(home_pt):<10}"

                # Totals
                totals = markets.get("totals", {})
                over = totals.get("Over", {}).get("point", "")
                total_str = f"{_format_point(over)}" if over else ""

                lines.append(f"  {title:<16} {ml_str}{spread_str}{total_str}")

            lines.append(f"  {'-' * 68}")

            # Best available
            if best_away_ml is not None:
                lines.append(
                    f"  Best: {away} ML {_format_odds(best_away_ml)} ({best_away_ml_book}), "
                    f"{home} ML {_format_odds(best_home_ml)} ({best_home_ml_book})"
                )

            # Live score if available
            live = game.get("live_score")
            if live:
                lines.append(
                    f"  LIVE: {away} {live.get('away_score', '?')} - "
                    f"{home} {live.get('home_score', '?')} "
                    f"({live.get('period', '')} {live.get('clock', '')})"
                )

            lines.append("")

        # Rate limit info
        rate_info = self.data.get("rate_limit", {})
        remaining = rate_info.get("requests_remaining", "?")
        lines.append(f"API quota remaining: {remaining}/500 requests this month")

        return "\n".join(lines)


def _format_odds(odds) -> str:
    """Format American odds with +/- prefix."""
    if odds == "" or odds is None:
        return "-"
    if isinstance(odds, (int, float)):
        return f"+{int(odds)}" if odds > 0 else str(int(odds))
    return str(odds)


def _format_point(point) -> str:
    """Format a point spread or total."""
    if point == "" or point is None:
        return "-"
    if isinstance(point, (int, float)):
        return f"+{point}" if point > 0 else str(point)
    return str(point)


class OddsTracker:
    """
    Main odds tracking orchestrator.

    Fetches odds, tracks line movements, detects sharp money signals,
    and produces formatted reports.
    """

    def __init__(
        self,
        odds_api_key: Optional[str] = None,
        shipp_api_key: Optional[str] = None,
        snapshots_dir: Optional[str] = None,
    ):
        """
        Initialize the odds tracker.

        Args:
            odds_api_key: The Odds API key. Falls back to ODDS_API_KEY env var.
            shipp_api_key: Shipp API key for live scores. Falls back to SHIPP_API_KEY.
            snapshots_dir: Directory for line movement snapshots.
        """
        self.odds_api = OddsAPI(api_key=odds_api_key)
        self.shipp = ShippClient(api_key=shipp_api_key)
        self.snapshots_dir = Path(snapshots_dir or SNAPSHOTS_DIR)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def get_odds(self, sport: str, markets: Optional[list] = None) -> OddsTable:
        """
        Get current odds for a sport with live score context.

        Args:
            sport: 'nba', 'mlb', 'soccer_epl', 'soccer_la_liga', etc.
            markets: Markets to fetch. Default: ['h2h', 'spreads', 'totals']

        Returns:
            OddsTable with formatted output.
        """
        # Fetch odds
        odds_data = self.odds_api.get_odds(sport, markets=markets)

        # Enrich with live scores from Shipp
        base_sport = sport.split("_")[0] if "_" in sport else sport
        game_context = self.shipp.get_game_context(base_sport)
        score_map = {}
        for game in game_context:
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()
            live = game.get("live_score")
            if live:
                score_map[f"{away}|{home}"] = live

        # Attach live scores to odds games
        for game in odds_data.get("games", []):
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()
            key = f"{away}|{home}"
            if key in score_map:
                game["live_score"] = score_map[key]

        odds_data["sport"] = sport
        odds_data["rate_limit"] = self.odds_api.get_rate_limit_status()

        return OddsTable(odds_data)

    def snapshot(self, sport: str, markets: Optional[list] = None):
        """
        Take a timestamped snapshot of current odds for line movement tracking.

        Snapshots are stored as JSON files in the snapshots directory.

        Args:
            sport: Sport to snapshot.
            markets: Markets to include.
        """
        odds_data = self.odds_api.get_odds(sport, markets=markets, use_cache=False)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        snapshot = {
            "sport": sport,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "games": odds_data.get("games", []),
        }

        filename = f"{sport}_{timestamp}.json"
        filepath = self.snapshots_dir / filename

        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=2)

        logger.info("Saved odds snapshot to %s", filepath)

    def _load_snapshots(self, sport: str) -> list:
        """Load all snapshots for a sport, sorted by time."""
        snapshots = []
        sport_prefix = f"{sport}_"

        for filepath in sorted(self.snapshots_dir.glob(f"{sport_prefix}*.json")):
            try:
                with open(filepath, "r") as f:
                    snapshots.append(json.load(f))
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Failed to load snapshot %s: %s", filepath, e)

        return snapshots

    def _get_opening_snapshot(self, sport: str) -> Optional[dict]:
        """Get the earliest (opening) snapshot for a sport."""
        snapshots = self._load_snapshots(sport)
        return snapshots[0] if snapshots else None

    def get_line_movement(self, sport: str, markets: Optional[list] = None) -> list:
        """
        Get line movement data comparing opening to current odds.

        Args:
            sport: Sport to analyze.
            markets: Markets to compare.

        Returns:
            List of game dicts with opening vs current odds and deltas.
        """
        # Get current odds
        current_data = self.odds_api.get_odds(sport, markets=markets)
        current_games = {g["game_id"]: g for g in current_data.get("games", [])}

        # Get opening snapshot
        opening = self._get_opening_snapshot(sport)
        opening_games = {}
        if opening:
            opening_games = {g["game_id"]: g for g in opening.get("games", [])}

        movements = []
        for game_id, current in current_games.items():
            opener = opening_games.get(game_id)
            if not opener:
                continue

            # Calculate consensus spread and total for both snapshots
            open_spread = _consensus_spread(opener)
            curr_spread = _consensus_spread(current)
            open_total = _consensus_total(opener)
            curr_total = _consensus_total(current)
            open_ml = _consensus_moneyline(opener)
            curr_ml = _consensus_moneyline(current)

            movement = {
                "game_id": game_id,
                "home": current.get("home_team"),
                "away": current.get("away_team"),
                "commence_time": current.get("commence_time"),
                "open_spread": open_spread,
                "current_spread": curr_spread,
                "spread_delta": (curr_spread - open_spread) if (
                    curr_spread is not None and open_spread is not None
                ) else None,
                "open_total": open_total,
                "current_total": curr_total,
                "total_delta": (curr_total - open_total) if (
                    curr_total is not None and open_total is not None
                ) else None,
                "open_moneyline": open_ml,
                "current_moneyline": curr_ml,
                "reverse_movement": False,
            }

            # Detect reverse line movement
            # RLM: line moves in a direction that contradicts expected public action
            if movement["spread_delta"] is not None and abs(movement["spread_delta"]) >= 1.0:
                movement["reverse_movement"] = True

            movements.append(movement)

        return movements

    def best_odds(self, sport: str, market: str = "h2h") -> list:
        """
        Find the best available odds across all bookmakers.

        Args:
            sport: Sport to check.
            market: Market type ('h2h', 'spreads', 'totals').

        Returns:
            List of games with best available odds per side.
        """
        odds_data = self.odds_api.get_odds(sport, markets=[market])
        results = []

        for game in odds_data.get("games", []):
            home = game.get("home_team")
            away = game.get("away_team")

            best_away = {"odds": None, "book": "", "point": None}
            best_home = {"odds": None, "book": "", "point": None}

            for bm in game.get("bookmakers", []):
                market_data = bm.get("markets", {}).get(market, {})
                title = bm.get("title", "Unknown")

                away_data = market_data.get(away, {})
                home_data = market_data.get(home, {})

                away_price = away_data.get("price")
                home_price = home_data.get("price")

                if away_price is not None:
                    if best_away["odds"] is None or away_price > best_away["odds"]:
                        best_away = {
                            "odds": away_price,
                            "book": title,
                            "point": away_data.get("point"),
                        }

                if home_price is not None:
                    if best_home["odds"] is None or home_price > best_home["odds"]:
                        best_home = {
                            "odds": home_price,
                            "book": title,
                            "point": home_data.get("point"),
                        }

            results.append({
                "game_id": game.get("game_id"),
                "home": home,
                "away": away,
                "commence_time": game.get("commence_time"),
                "best_away_odds": best_away["odds"],
                "best_away_book": best_away["book"],
                "best_away_point": best_away["point"],
                "best_home_odds": best_home["odds"],
                "best_home_book": best_home["book"],
                "best_home_point": best_home["point"],
            })

        return results

    def sharp_money_alerts(self, sport: str) -> list:
        """
        Identify potential sharp money movements.

        Sharp money signals:
        - Reverse line movement (line moves against public side)
        - Significant spread changes (>= 1.5 points)
        - Total movement of >= 2 points

        Args:
            sport: Sport to analyze.

        Returns:
            List of alert dicts with signal details.
        """
        movements = self.get_line_movement(sport)
        alerts = []

        for game in movements:
            away = game.get("away", "Away")
            home = game.get("home", "Home")
            game_label = f"{away} @ {home}"

            # Check spread movement
            spread_delta = game.get("spread_delta")
            if spread_delta is not None and abs(spread_delta) >= 1.5:
                if spread_delta > 0:
                    direction = f"{home} favored more (spread moved from {game['open_spread']} to {game['current_spread']})"
                else:
                    direction = f"{away} favored more (spread moved from {game['open_spread']} to {game['current_spread']})"

                alerts.append({
                    "game": game_label,
                    "market": "spread",
                    "signal": "significant_spread_movement",
                    "open": game["open_spread"],
                    "current": game["current_spread"],
                    "delta": spread_delta,
                    "direction": direction,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "confidence": "high" if abs(spread_delta) >= 2.0 else "medium",
                })

            # Check total movement
            total_delta = game.get("total_delta")
            if total_delta is not None and abs(total_delta) >= 2.0:
                direction = "over" if total_delta > 0 else "under"
                alerts.append({
                    "game": game_label,
                    "market": "total",
                    "signal": "significant_total_movement",
                    "open": game["open_total"],
                    "current": game["current_total"],
                    "delta": total_delta,
                    "direction": f"Total moved {direction} by {abs(total_delta)} points",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "confidence": "medium",
                })

            # Check reverse line movement
            if game.get("reverse_movement"):
                alerts.append({
                    "game": game_label,
                    "market": "spread",
                    "signal": "reverse_line_movement",
                    "open": game["open_spread"],
                    "current": game["current_spread"],
                    "delta": spread_delta,
                    "direction": "Line moved against expected public betting side",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "confidence": "medium",
                })

        return alerts

    def dashboard(self, sports: Optional[list] = None) -> str:
        """
        Generate a full multi-sport odds dashboard.

        Args:
            sports: List of sports. Default: ['nba', 'mlb', 'soccer_epl']

        Returns:
            Formatted string with all odds tables.
        """
        if sports is None:
            sports = ["nba", "mlb", "soccer_epl"]

        sections = []
        for sport in sports:
            try:
                odds_table = self.get_odds(sport)
                sections.append(odds_table.table())
            except Exception as e:
                sections.append(f"=== {sport.upper()} === \nError: {e}\n")

        # Add alerts
        all_alerts = []
        for sport in sports:
            try:
                alerts = self.sharp_money_alerts(sport)
                all_alerts.extend(alerts)
            except Exception:
                pass

        if all_alerts:
            sections.append("=== SHARP MONEY ALERTS ===")
            sections.append("")
            for alert in all_alerts:
                sections.append(
                    f"  [{alert['confidence'].upper()}] {alert['game']} -- "
                    f"{alert['signal']} ({alert['market']})"
                )
                sections.append(
                    f"    {alert['direction']}"
                )
            sections.append("")

        return "\n".join(sections)


# ---------------------------------------------------------------------------
# Helper functions for consensus calculations
# ---------------------------------------------------------------------------

def _consensus_spread(game: dict) -> Optional[float]:
    """Calculate the consensus spread from all bookmakers."""
    spreads = []
    home = game.get("home_team", "")

    for bm in game.get("bookmakers", []):
        market = bm.get("markets", {}).get("spreads", {})
        home_data = market.get(home, {})
        point = home_data.get("point")
        if point is not None:
            spreads.append(float(point))

    if not spreads:
        return None
    return round(sum(spreads) / len(spreads), 1)


def _consensus_total(game: dict) -> Optional[float]:
    """Calculate the consensus total from all bookmakers."""
    totals = []

    for bm in game.get("bookmakers", []):
        market = bm.get("markets", {}).get("totals", {})
        over_data = market.get("Over", {})
        point = over_data.get("point")
        if point is not None:
            totals.append(float(point))

    if not totals:
        return None
    return round(sum(totals) / len(totals), 1)


def _consensus_moneyline(game: dict) -> Optional[dict]:
    """Calculate consensus moneyline from all bookmakers."""
    home = game.get("home_team", "")
    away = game.get("away_team", "")
    home_prices = []
    away_prices = []

    for bm in game.get("bookmakers", []):
        market = bm.get("markets", {}).get("h2h", {})
        home_price = market.get(home, {}).get("price")
        away_price = market.get(away, {}).get("price")
        if home_price is not None:
            home_prices.append(home_price)
        if away_price is not None:
            away_prices.append(away_price)

    if not home_prices and not away_prices:
        return None

    return {
        "home": round(sum(home_prices) / len(home_prices)) if home_prices else None,
        "away": round(sum(away_prices) / len(away_prices)) if away_prices else None,
    }


def main():
    """CLI entry point for the odds tracker."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Betting Odds Tracker -- Compare odds across sportsbooks"
    )
    parser.add_argument(
        "--sport",
        choices=["nba", "mlb", "soccer_epl", "soccer_la_liga", "soccer_ucl", "soccer_mls", "all"],
        default="all",
        help="Sport to track (default: all)",
    )
    parser.add_argument(
        "--action",
        choices=["odds", "snapshot", "movement", "best", "alerts", "dashboard"],
        default="dashboard",
        help="Action to perform (default: dashboard)",
    )
    parser.add_argument(
        "--market",
        choices=["h2h", "spreads", "totals"],
        default="h2h",
        help="Market for best-odds lookup (default: h2h)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    tracker = OddsTracker()

    if args.action == "dashboard":
        sports = None if args.sport == "all" else [args.sport]
        print(tracker.dashboard(sports=sports))

    elif args.action == "odds":
        sport = args.sport if args.sport != "all" else "nba"
        odds = tracker.get_odds(sport)
        if args.format == "json":
            print(json.dumps(odds.data, indent=2))
        else:
            print(odds.table())

    elif args.action == "snapshot":
        if args.sport == "all":
            for s in ["nba", "mlb", "soccer_epl"]:
                tracker.snapshot(s)
        else:
            tracker.snapshot(args.sport)
        print("Snapshot saved.")

    elif args.action == "movement":
        sport = args.sport if args.sport != "all" else "nba"
        movements = tracker.get_line_movement(sport)
        if args.format == "json":
            print(json.dumps(movements, indent=2))
        else:
            for m in movements:
                print(f"{m['away']} @ {m['home']}")
                if m.get("spread_delta") is not None:
                    print(f"  Spread: {m['open_spread']} -> {m['current_spread']} (delta: {m['spread_delta']:+.1f})")
                if m.get("total_delta") is not None:
                    print(f"  Total:  {m['open_total']} -> {m['current_total']} (delta: {m['total_delta']:+.1f})")
                if m.get("reverse_movement"):
                    print("  ** REVERSE LINE MOVEMENT **")
                print()

    elif args.action == "best":
        sport = args.sport if args.sport != "all" else "nba"
        best = tracker.best_odds(sport, market=args.market)
        if args.format == "json":
            print(json.dumps(best, indent=2))
        else:
            for b in best:
                print(f"{b['away']} @ {b['home']}")
                print(f"  Best {b['away']}: {_format_odds(b['best_away_odds'])} at {b['best_away_book']}")
                print(f"  Best {b['home']}: {_format_odds(b['best_home_odds'])} at {b['best_home_book']}")
                print()

    elif args.action == "alerts":
        if args.sport == "all":
            for s in ["nba", "mlb", "soccer_epl"]:
                alerts = tracker.sharp_money_alerts(s)
                for a in alerts:
                    print(f"[{a['confidence'].upper()}] {a['game']} -- {a['signal']}")
                    print(f"  {a['direction']}")
        else:
            alerts = tracker.sharp_money_alerts(args.sport)
            for a in alerts:
                print(f"[{a['confidence'].upper()}] {a['game']} -- {a['signal']}")
                print(f"  {a['direction']}")


if __name__ == "__main__":
    main()
