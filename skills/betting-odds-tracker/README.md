# betting-odds-tracker

**Track where the smart money is going.**

A real-time odds monitoring tool that tracks line movements across sportsbooks,
identifies sharp money signals, and helps you find the best available odds for
NBA, MLB, and Soccer.

## The Problem

Odds are scattered across dozens of sportsbooks. Lines move fast. By the time
you manually compare FanDuel, DraftKings, and BetMGM, the value is gone. And
you have no idea whether that line move was driven by public money or sharps.

## The Solution

`betting-odds-tracker` pulls odds from multiple sportsbooks through The Odds
API, tracks every movement over time, and alerts you to sharp money signals --
all correlated with live game scores so you have full context.

### What You Get

- **Cross-book comparison**: See moneyline, spread, and totals from every
  major sportsbook side by side
- **Line movement tracking**: Opening vs current line with timestamped
  snapshots
- **Sharp money detection**: Reverse line movement flags (when lines move
  against public betting percentages)
- **Best available odds**: Instantly see which book has the best price
- **Live game context**: Current scores pulled alongside odds for in-game
  correlation

## Who This Is For

- **Sports bettors** who want to find the best line before placing a wager
- **Sharp bettors** who track line movement as an information signal
- **Casual bettors** who want to stop leaving money on the table by always
  betting at the worst number
- **Analysts** studying market efficiency in sports betting

## Setup

### 1. The Odds API Key (Required)

The odds data comes from [The Odds API](https://the-odds-api.com). The free
tier provides 500 API requests per month, which is plenty for daily tracking.

1. Register at [the-odds-api.com](https://the-odds-api.com)
2. Copy your API key from the dashboard
3. Set the environment variable:

```bash
export ODDS_API_KEY="your-odds-api-key-here"
```

### 2. Data API Key for Live Scores (Required)

For live game score context:

```bash
export SHIPP_API_KEY="your-api-key-here"
```

### 3. Install Dependencies

```bash
pip install requests
```

## Usage

### Quick Odds Check

```python
from scripts.odds_tracker import OddsTracker

tracker = OddsTracker()

# Get current NBA odds
nba_odds = tracker.get_odds("nba")
print(nba_odds.table())
```

### Track Line Movement

```python
# Take a snapshot (run this periodically, e.g., every hour)
tracker.snapshot("nba")

# View movement since opening
movements = tracker.get_line_movement("nba")
for game in movements:
    print(f"{game['away']} @ {game['home']}")
    print(f"  Spread: opened {game['open_spread']} -> now {game['current_spread']}")
    print(f"  Total:  opened {game['open_total']} -> now {game['current_total']}")
    if game.get("reverse_movement"):
        print("  ** REVERSE LINE MOVEMENT DETECTED **")
```

### Find Best Odds

```python
best = tracker.best_odds("nba", market="h2h")
for game in best:
    print(f"{game['away']} @ {game['home']}")
    print(f"  Best {game['away']}: {game['best_away_odds']} at {game['best_away_book']}")
    print(f"  Best {game['home']}: {game['best_home_odds']} at {game['best_home_book']}")
```

### Sharp Money Alerts

```python
alerts = tracker.sharp_money_alerts("nba")
for alert in alerts:
    print(f"ALERT: {alert['game']} -- {alert['signal']}")
    print(f"  Line moved from {alert['open']} to {alert['current']}")
    print(f"  Direction: {alert['direction']}")
```

### All Sports Dashboard

```python
dashboard = tracker.dashboard()
print(dashboard)
```

## Output Format

### Odds Table

```
=== NBA ODDS — February 18, 2026 ===

Lakers @ Warriors — 7:30 PM ET
                  Moneyline         Spread           Total
                  LAL    GSW       LAL    GSW       O/U
  FanDuel        +145   -170      +4.0   -4.0      225.5
  DraftKings     +150   -175      +4.0   -4.0      226.0
  BetMGM         +140   -165      +3.5   -3.5      225.5
  Caesars        +145   -170      +4.0   -4.0      225.0
  -----------------------------------------------------------------
  Best Odds      +150   -165      +4.0   -3.5      226.0/225.0
  Consensus      +145   -170      +4.0   -4.0      225.5
  Opening        +130   -155      +3.0   -3.0      223.5
  Movement         +15    -15      +1.0   -1.0       +2.0
```

### Movement Alert

```json
{
  "game": "Lakers @ Warriors",
  "market": "spread",
  "signal": "reverse_line_movement",
  "open": -3.0,
  "current": -4.0,
  "direction": "Warriors favored more despite public on Lakers",
  "timestamp": "2026-02-18T18:30:00Z",
  "confidence": "medium"
}
```

## API Usage & Rate Limits

The free tier of The Odds API allows 500 requests per month. The tracker is
designed to be efficient with your quota:

| Action                    | API Calls | Recommended Frequency |
|---------------------------|-----------|-----------------------|
| All NBA odds (3 markets)  | 1         | Every 2-4 hours       |
| All MLB odds (3 markets)  | 1         | Every 2-4 hours       |
| All Soccer odds (1 league)| 1         | Every 2-4 hours       |
| Score settlement check    | 1         | After games end       |

With 3 sports checked 4x/day, you use roughly 360 requests/month, leaving
headroom for ad-hoc checks.

The tracker also caches responses locally (15-minute TTL) to avoid duplicate
API calls within short windows.

## Architecture

```
odds_tracker.py         Main orchestrator + reporting
    |
    +---> odds_api.py       The Odds API integration + caching
    |
    +---> shipp_wrapper.py  Live score context
    |
    +---> snapshots/        Local line movement history (JSON)
```

## Supported Sportsbooks

The Odds API returns odds from many US-licensed sportsbooks. Common ones:

- FanDuel
- DraftKings
- BetMGM
- Caesars (William Hill)
- PointsBet
- Barstool / ESPN BET
- BetRivers
- Unibet

Availability varies by sport and region.

## License

MIT
