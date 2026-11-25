"""Experimental PAPER-only intraminute entry signals from Coinbase Exchange ticker.

Observed ticker trades are a *sample* of activity, NOT authoritative exchange-wide
volume. Warm up before issuing signals; never invent history on reconnection.
"""
from __future__ import annotations
from collections import defaultdict, deque
import math
import time

MIN_WARMUP = 150.0
WINDOW = 240.0

class LiveEntryTracker:
    def __init__(self):
        self.ticks = defaultdict(deque)
        self.started = {}
        self.last_trade_id = {}
        self.last_signal = {}

    def reset(self):
        """Reset on WebSocket reconnection so stale periods cannot generate signals."""
        self.ticks.clear()
        self.started.clear()
        self.last_trade_id.clear()
        self.last_signal.clear()

    def observe(self, pair, q, now=None):
        """Record an observed ticker trade; return per-pair metrics when warmed up."""
        now = time.monotonic() if now is None else now
        bid, ask = q.get('bid', 0), q.get('ask', 0)
        price, size = q.get('trade_price'), q.get('last_size')
        trade_id = q.get('trade_id')
        if (price is None or size is None or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (bid,ask,price,size))
                or bid <= 0 or ask < bid or price <= 0 or size <= 0):
            return None
        if trade_id is not None and self.last_trade_id.get(pair) == trade_id:
            return None
        self.last_trade_id[pair] = trade_id
        self.started.setdefault(pair, now)
        dq = self.ticks[pair]
        dq.append((now, price, price*size))
        while dq and now-dq[0][0] > WINDOW: dq.popleft()
        if now-self.started[pair] < MIN_WARMUP: return None
        older = [x for x in dq if 180 <= now-x[0] <= 240]
        prev = [x for x in dq if 60 < now-x[0] <= 120]
        current = [x for x in dq if now-x[0] <= 60]
        if not older or len(prev) < 3 or len(current) < 5:
            return None
        ref1 = next((x[1] for x in reversed(dq) if now-x[0] >= 60), None)
        ref3 = next((x[1] for x in reversed(dq) if now-x[0] >= 180), None)
        if not ref1 or not ref3: return None
        v_current = sum(x[2] for x in current)
        v_prev = sum(x[2] for x in prev)
        if v_prev < 100: return None
        return {'m1':price/ref1-1, 'm3':price/ref3-1,
                'volume_ratio':v_current/v_prev,
                'observed_usd_1m':v_current, 'ticks_1m':len(current)}

    def select(self, pair, q, metrics, name, now=None):
        """Conservative, explicitly experimental triggers; NO order placement."""
        if not metrics: return False
        now = time.monotonic() if now is None else now
        spread = q.get('spread', 1)
        if spread > .008 or now-self.last_signal.get((name,pair),-1e9) < 180:
            return False
        if name == 'New Coin Sniper':
            valid = (0.012 <= metrics['m1'] <= .15 and .018 <= metrics['m3'] <= .30
                     and metrics['volume_ratio'] >= 1.6 and metrics['observed_usd_1m'] >= 3000
                     and metrics['ticks_1m'] >= 8)
        elif name == 'All on Black':
            valid = (0.018 <= metrics['m1'] <= .15 and .03 <= metrics['m3'] <= .30
                     and metrics['volume_ratio'] >= 2.1 and metrics['observed_usd_1m'] >= 10000
                     and metrics['ticks_1m'] >= 12)
        else:
            return False
        if valid: self.last_signal[(name,pair)] = now
        return valid
