# Coinbase Four-Bot Paper Trading Competition (Windows)

**PAPER ONLY. All four $1,000 accounts are imaginary, with no real money at risk.** This program has **no Coinbase API-key authentication, wallet connection, or order submission code**. It uses Coinbase Exchange public market data, which may not precisely match the assets available in your individual Coinbase Advanced account.

## Installation

1. Install Python 3.11+ from https://www.python.org/downloads/windows/ (check the launcher option).
2. Extract the ZIP into a normal Windows folder such as `Documents\CoinbaseCompetition`.
3. Double-click `install_windows.bat` to create the Python environment and install the dependencies.
4. Double-click `run_one_scan.bat` for a connectivity test (scan can take a minute or more). This creates the `data` folder.
5. Double-click `open_dashboard.bat` for the leaderboard, or open `data\dashboard.html` in a browser.
6. Double-click `run_competition.bat` to scan repeatedly. Leave the command window running and Windows awake. To stop, press Ctrl+C.

`run_competition.bat` launches a browser if `data/dashboard.html` exists; run a single scan first to create it. The dashboard auto-refreshes every 60 seconds but updates only after a scan finishes. If a Windows association error occurs, double-click `data\dashboard.html` directly.

The simulator preserves the four accounts in `data\competition_state.json`. Trade events, top signals, and periodic leaderboard snapshots are in `data\trades.csv`, `data\signals.csv`, and `data\leaderboard.csv`. Opening those files in Excel while the bot runs may block writing; close Excel before the next scan.

## Four contestants

All bots scan **the same rotating slice of up to 25 Coinbase USD pairs per scan**, with the same raw 15-minute candles, live bid/ask quotes, assumptions, capital, position limits and exits. This is **not a simultaneous scan of every Coinbase coin**.

- **Momentum Hunter:** strong positive 1-hour/4-hour returns, price above 20-bar average and unusual volume.
- **Breakout Trader:** fresh high over the previous 20 completed candles, with positive momentum and unusual volume.
- **Dip Buyer:** a recent pullback from a 24-hour high followed by a partial bounce, positive 1-hour price movement and a volume pickup.
- **Hybrid AI:** same algorithmic momentum shortlist as Momentum Hunter, then an **actual OpenAI model** approves or rejects the candidate. **Until an API key is configured, Hybrid AI has no new trades**; it is not secretly substituted with rules-only trading. AI errors also block new Hybrid AI entries.

Each bot starts with a virtual **$1,000**, can hold at most **four $50 positions**, and has a **$200 exposure limit**. Stops are simulated at the latest available quote: 15% loss threshold, and a 7% trailing exit after a 12% peak gain. A $30 same-UTC-day equity decline pauses new entries for that bot; paused bots stay paused until a reset or manual investigation. Existing positions are still checked for exits. Paper stops and fills cannot guarantee live execution.

The simulator models **0.6% fee per side and 0.2% slippage per side**. Those are placeholders; actual Coinbase pricing and slippage differ. Simulation may overestimate profits from volatile illiquid coins. Winning a short contest doesn't demonstrate a market edge; compare longer periods, net fees, max drawdown and a simple buy-and-hold benchmark before considering real trading.

## Optional AI contestant (API usage can cost money)

1. Copy `.env.example` to `.env` in the main folder.
2. Set `OPENAI_API_KEY` to your separately purchased OpenAI API key. ChatGPT Plus is not an API subscription.
3. Optionally specify `OPENAI_MODEL` (default in config: `gpt-4.1-mini`). **Confirm that the model is available to your account**.
4. Restart the program. AI reviews only numeric features of algorithmically selected coins; it cannot inspect news, predict spikes, or bypass controls. Consider API spending limits before enabling.

Never add Coinbase API keys. Do not upload `.env` or share API keys in screenshots.

## Other controls

Edit `config.json` while the program is stopped. `status.bat` shows saved results. To reset **all four** virtual accounts, run `.venv\Scripts\python.exe competition.py --reset --once` in a Windows terminal opened in this folder. The program backs up the prior state and CSV/HTML reports before restarting. Backups are not part of a formal audit database.

**Important caveats:** (1) rotating scans can miss rapidly moving coins; (2) completed 15-minute candles lag market events; (3) public API outages, missing or wide quotes, rate limits and Windows sleep can cause missed exits; (4) quotes may be stale and marks estimated, not guaranteed executable fills; (5) the bots share a market feed but simulate independent accounts, not four real accounts. No live trading functionality is included. The package is educational, not investment advice, and no strategy has been validated for profitability.

Official API background: https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproducts
