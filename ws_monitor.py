"""Paper-only Coinbase Exchange ticker stream for all paper-bot positions.

No exchange authentication, order methods, or real-money trading functionality.
Uses ticker best_bid/best_ask (never last trade price) for simulated risk exits.
"""
from __future__ import annotations
import json
import math
import threading
import time

URL = 'wss://ws-feed.exchange.coinbase.com'


def ticker_quote(message):
    """Accept only actionable positive two-sided bid/ask Exchange ticker messages."""
    if message.get('type') != 'ticker':
        return None
    try:
        bid = float(message.get('best_bid'))
        ask = float(message.get('best_ask'))
        if not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
            return None
        return {'bid': bid, 'ask': ask, 'spread': (ask - bid) / ((ask + bid) / 2)}
    except (ValueError, TypeError, ZeroDivisionError):
        return None


class PaperPriceMonitor:
    """Background monitor. Modifications to state occur ONLY through on_quote callback.

    current_pairs is queried from the shared, locked state. Subscribe/reconnect when
    the held-pair set changes, including when a position is opened or closed.
    """
    def __init__(self, current_pairs, on_quote, log=print, poll_seconds=2):
        self.current_pairs = current_pairs
        self.on_quote = on_quote
        self.log = log
        self.poll_seconds = poll_seconds
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is not None:
            raise RuntimeError('Monitor already running')
        self.thread = threading.Thread(target=self.run, name='paper-websocket-monitor', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def run(self):
        import websocket  # pip install websocket-client; never require auth
        backoff = 2
        while not self.stopping.is_set():
            pairs = sorted(set(self.current_pairs()))
            if not pairs:
                self.stopping.wait(self.poll_seconds)
                continue
            sock = None
            try:
                sock = websocket.create_connection(URL, timeout=self.poll_seconds, enable_multithread=True)
                sock.send(json.dumps({'type': 'subscribe', 'product_ids': pairs,
                                      'channels': ['ticker', 'heartbeat']}))
                self.log(f'  WS PAPER monitoring: {", ".join(pairs)}')
                backoff = 2
                last_message = time.monotonic()
                while not self.stopping.is_set():
                    if sorted(set(self.current_pairs())) != pairs:
                        break  # dynamically resubscribe to the new held pair
                    try:
                        raw = sock.recv()
                    except websocket.WebSocketTimeoutException:
                        # If the socket is quiet, reconnect; no fabricated bid quote.
                        if time.monotonic() - last_message > 35:
                            raise TimeoutError('WebSocket data stale >35 seconds')
                        continue
                    if raw is None or raw == '':
                        raise ConnectionError('WebSocket closed')
                    msg = json.loads(raw)
                    if msg.get('type') == 'error':
                        raise ConnectionError(str(msg.get('message', 'subscription error'))[:160])
                    if msg.get('type') in ('heartbeat', 'ticker'):
                        last_message = time.monotonic()
                    pair = msg.get('product_id')
                    if pair not in pairs:
                        continue
                    q = ticker_quote(msg)
                    if q is not None:
                        self.on_quote(pair, q)
            except Exception as e:
                self.log(f'  WS WARN: {type(e).__name__}: {e}; REST scanner remains active')
                self.stopping.wait(backoff)
                backoff = min(30, backoff * 2)
            finally:
                if sock is not None:
                    try: sock.close()
                    except Exception: pass
