"""Read-only JSON export of simulated Coinbase bot performance. No credentials or config."""
import argparse
import csv
import json
import math
import os
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

def number(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else 0.0
    except (TypeError, ValueError):
        return 0.0

def tail_csv(path, limit, fields):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [{field: row.get(field, "") for field in fields}
                for row in deque(csv.DictReader(f), maxlen=limit)]

def report(data=DATA):
    state = json.loads((data / "competition_state.json").read_text(encoding="utf-8"))
    bots = {}
    for name, bot in state.get("bots", {}).items():
        positions = {}
        for pair, p in bot.get("positions", {}).items():
            positions[pair] = {
                "quantity": number(p.get("qty")),
                "cost_usd": number(p.get("cost")),
                "entry_price": number(p.get("entry")),
                "last_bid": number(p.get("last_bid")),
                "highest_bid": number(p.get("highest_bid")),
                "entry_utc": p.get("entered"),
            }
        bots[name] = {
            "equity_usd": number(bot.get("last_equity")),
            "cash_usd": number(bot.get("cash")),
            "realized_pnl_usd": number(bot.get("realized_pnl")),
            "wins": int(bot.get("wins", 0)),
            "losses": int(bot.get("losses", 0)),
            "max_drawdown_fraction": number(bot.get("max_drawdown")),
            "paused": bool(bot.get("paused")),
            "positions": positions,
        }
    return {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "SIMULATED Coinbase paper trading; no real orders",
        "cycle": int(state.get("cycle", 0)),
        "bots": bots,
        "recent_trades": tail_csv(data / "trades.csv", 80,
            ["utc", "bot", "action", "pair", "qty", "price", "fee", "net_pnl", "reason"]),
        "recent_snapshots": tail_csv(data / "leaderboard.csv", 180,
            ["utc", "cycle", "bot", "equity", "cash", "positions", "realized_pnl", "max_drawdown", "paused"]),
    }

def write_atomic(path, payload):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, allow_nan=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        import tempfile as tf
        with tf.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "competition_state.json").write_text(json.dumps({
                "cycle": 1, "bots": {"Test": {
                    "cash": 950, "last_equity": 1001, "positions": {
                        "TEST-USD": {"qty": 5, "cost": 50, "entry": 10, "last_bid": 10.2}
                    }
                }}
            }))
            data = report(d)
            assert data["bots"]["Test"]["equity_usd"] == 1001
            write_atomic(d / "latest.json", data)
            assert json.loads((d / "latest.json").read_text())["cycle"] == 1
        print("PASS: read-only JSON report and atomic write")
    else:
        if not args.output:
            ap.error("--output is required")
        write_atomic(args.output, report())
        print("Report created:", args.output)
