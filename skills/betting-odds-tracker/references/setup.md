# Setup Guide — betting-odds-tracker Skill

This guide walks you through configuring all required and optional API keys
for the `betting-odds-tracker` skill.

## Required: Shipp.ai API Key

The Shipp.ai API key is required for live scores, game schedules, and
real-time event data used alongside odds for tracking and analysis.

### Steps

1. **Create an account** at [platform.shipp.ai](https://platform.shipp.ai)
2. **Sign in** and navigate to **Settings > API Keys**
3. **Generate a new API key** — copy it immediately (it won't be shown again)
4. **Set the environment variable**:

```bash
# Add to your shell profile (~/.zshrc, ~/.bashrc, etc.)
export SHIPP_API_KEY="shipp_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

5. **Verify** by running:

```bash
curl -s -H "Authorization: Bearer $SHIPP_API_KEY" \
  "https://api.shipp.ai/api/v1/connections" | python3 -m json.tool
```

You should see a JSON response (even if the connections list is empty).

### API Key Format

Shipp API keys typically start with `shipp_live_` or `shipp_test_`. Use the
`live` key for production sports data.

### Rate Limits

Your rate limit depends on your Shipp.ai plan:

| Plan       | Requests/min | Connections | Notes                    |
|------------|-------------|-------------|--------------------------|
| Free       | 30          | 3           | Great for trying it out  |
| Starter    | 120         | 10          | Suitable for one sport   |
| Pro        | 600         | 50          | All three sports         |
| Enterprise | Custom      | Unlimited   | Contact sales            |

## Required: The Odds API Key

The Odds API key is required for fetching live and pre-game betting odds
from multiple sportsbooks. This is a core dependency of the skill.

### Steps

1. **Register** at [the-odds-api.com](https://the-odds-api.com)
2. **Sign in** and copy your API key from the dashboard
3. **Set the environment variable**:

```bash
export ODDS_API_KEY="your-odds-api-key"
```

4. **Verify** by running:

```bash
curl -s "https://api.the-odds-api.com/v4/sports/?apiKey=$ODDS_API_KEY" \
  | python3 -m json.tool
```

You should see a JSON list of available sports.

### Free Tier Limits

- 500 requests per month
- Access to all sports and regions
- Moneyline, spread, and totals markets
- Usage counter returned in response headers (`x-requests-remaining`)

### Paid Plans

If you need more than 500 requests/month, The Odds API offers paid tiers.
Check [the-odds-api.com/#pricing](https://the-odds-api.com/#pricing) for
current plans and pricing.

## Python Dependencies

Install the required package:

```bash
pip install requests
```

All other dependencies are from the Python standard library (`os`, `time`,
`logging`, `datetime`, `json`, `typing`).

## Environment Variable Summary

| Variable        | Required | Source              | Purpose                           |
|-----------------|----------|---------------------|-----------------------------------|
| `SHIPP_API_KEY` | Yes      | platform.shipp.ai   | Live scores, schedule, events     |
| `ODDS_API_KEY`  | Yes      | the-odds-api.com     | Betting odds from sportsbooks     |

## Verifying Your Setup

Run the built-in smoke test:

```bash
cd skills/community/betting-odds-tracker
python3 scripts/odds_tracker.py --once
```

This will attempt to:
1. Fetch live game data (requires `SHIPP_API_KEY`)
2. Fetch current betting odds (requires `ODDS_API_KEY`)
3. Cross-reference scores with odds movements
4. Display a summary of available odds for today's games

Each section will show either data or an error message indicating which
key is missing or which service is unavailable.

## Troubleshooting

### "SHIPP_API_KEY environment variable is not set"

Your shell session doesn't have the key. Make sure you either:
- Added `export SHIPP_API_KEY=...` to your shell profile and restarted the terminal
- Or ran the export command in the current session

### "ODDS_API_KEY environment variable is not set"

The Odds API key is required for this skill to function. Follow the steps
in the "Required: The Odds API Key" section above.

### "Shipp API 401: Unauthorized"

The key is set but invalid. Double-check:
- No extra spaces or newline characters in the key
- The key is from the correct environment (live vs test)
- The key hasn't been revoked

### "Shipp API 402: Payment Required"

Your plan's quota has been exceeded. Check your usage at
[platform.shipp.ai/usage](https://platform.shipp.ai) or upgrade your plan.

### "Shipp API 429: Too Many Requests"

You've hit the rate limit. The tracker automatically retries with backoff,
but if it persists, reduce polling frequency or upgrade your plan.

### "Odds API 401: Invalid API Key"

Double-check your `ODDS_API_KEY` value. Ensure there are no extra spaces or
characters. Regenerate the key from the-odds-api.com dashboard if needed.

### "Odds API 429: Out of requests"

The free tier allows 500 requests per month. Check your remaining quota via
the `x-requests-remaining` response header or on your dashboard. Upgrade
your plan or wait until the monthly quota resets.

## Documentation Links

- **Shipp.ai Docs**: [docs.shipp.ai](https://docs.shipp.ai)
- **Shipp.ai API Reference**: [docs.shipp.ai/api](https://docs.shipp.ai/api)
- **The Odds API Docs**: [the-odds-api.com/liveapi/guides/v4](https://the-odds-api.com/liveapi/guides/v4/)
- **The Odds API Sports List**: [the-odds-api.com/sports-odds-data](https://the-odds-api.com/sports-odds-data/)
