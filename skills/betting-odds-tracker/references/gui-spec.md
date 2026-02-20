# GUI Specification: betting-odds-tracker

## Design Direction
- **Style:** Data-dense grid layout inspired by financial trading terminals. Odds tables dominate the view with inline sparkline charts showing historical line movement. Every cell is information-rich, using color and directional arrows to convey movement at a glance. Built for sharp-eyed bettors who need to spot value fast.
- **Inspiration:** Pinnacle/Betfair exchange grid interface, TradingView chart panels on Dribbble, Robinhood stock ticker movement indicators on Behance
- **Mood:** Sharp, precise, high-frequency, profitable

## Layout
- **Primary view:** Full-width data grid (80%) with collapsible detail panel (20% right). Grid shows odds table: rows are games, columns are sportsbooks. Each cell contains current odds + movement arrow + mini sparkline. Detail panel shows selected game's full line history chart, book comparison, and value analysis. Top bar (48px) for sport/date filters.
- **Mobile:** Horizontal scrollable table with sticky first column (game matchup). Detail panel becomes a bottom sheet that slides up on game row tap. Sparklines hidden on mobile, replaced by colored movement arrows only.
- **Header:** Compact filter bar with: sport selector pills (left), date picker (center), odds format toggle (American/Decimal/Fractional) and refresh indicator (right).

## Color Palette
- Background: #0C0A09 (True Black)
- Surface: #1C1917 (Stone Dark)
- Primary accent: #22C55E (Money Green) — favorable line movement, positive value, best odds highlight
- Success: #22C55E (Money Green) — odds moving in bettor's favor, sharp money indicators
- Warning: #EF4444 (Loss Red) — unfavorable movement, odds shortening, steam moves against
- Text primary: #FAFAF9 (Warm White)
- Text secondary: #A8A29E (Stone Grey)

## Component Structure
- **OddsGrid** — Full-width responsive table. Rows: games (team matchup + time). Columns: sportsbooks (DraftKings, FanDuel, BetMGM, etc.). Cells contain OddsCell components. Sticky header row and first column.
- **OddsCell** — Individual cell showing: current odds value (bold), movement arrow (green up = favorable, red down = unfavorable), and a 24h sparkline micro-chart (30px wide). Best available odds across books highlighted with green background tint.
- **SparklineChart** — Tiny inline SVG line chart (30x16px in cells, 100% width in detail panel) showing odds movement over time. Green line if current is better than open, red if worse. Hover reveals value at point.
- **BookComparisonBar** — In the detail panel, horizontal bar chart comparing current odds across all tracked sportsbooks for a selected market. Best value book highlighted.
- **MovementArrow** — Directional indicator: green up-arrow for line moving favorably, red down-arrow for unfavorable, grey dash for no movement. Includes delta value on hover.
- **MarketTabs** — Tab row to switch between market types: Spread, Moneyline, Total (Over/Under), Props. Active tab has green underline.
- **SteamMoveAlert** — Small flash indicator on cells where sharp/steam money has been detected (rapid line movement across multiple books). Pulsing amber border for 10s after detection.

## Typography
- Headings: Inter Semi-Bold, 16-20px, letter-spacing -0.01em, #FAFAF9
- Body: Inter Regular, 13-14px, line-height 1.4, #A8A29E for secondary, #FAFAF9 for primary
- Stats/numbers: JetBrains Mono Bold, 14-16px for odds values, 11-12px for sparkline labels, tabular-nums enabled, monospace alignment for columns

## Key Interactions
- **Cell hover:** Hovering an OddsCell expands it slightly, reveals the full sparkline with time axis, shows opening odds vs current, and highlights the delta value.
- **Row selection:** Clicking a game row selects it and populates the right detail panel with full line history charts, book comparison, and value analysis. Selected row has a subtle green left-border glow.
- **Market tab switch:** Switching between Spread/Moneyline/Total/Props re-renders all grid cells with a 150ms number-flip animation as values change to the new market type.
- **Best odds highlight:** The best available odds across sportsbooks for each game auto-highlights with a green background tint. Toggling "Show best odds only" collapses the grid to show only the best available line per game.
- **Steam move alert:** When a steam move is detected, the affected cells flash with an amber border pulse (3 cycles over 10s) and a small "STEAM" badge appears temporarily.
- **Odds format toggle:** Switching between American (-110), Decimal (1.91), and Fractional (10/11) formats re-renders all odds with a subtle cross-fade transition.

## Reference Screenshots
- [Pinnacle Odds Grid Interface on Dribbble](https://dribbble.com/search/betting-odds-grid-dark) — Data-dense odds comparison grid with multi-book columns and line movement indicators
- [TradingView Chart Panels on Behance](https://www.behance.net/search/projects?search=trading+dashboard+dark) — Sparkline charts and financial data grid layout inspiration for odds movement visualization
- [Robinhood Movement Indicators on Mobbin](https://mobbin.com/search/stock-price-movement) — Color-coded directional movement arrows and inline trend charts for real-time value tracking
