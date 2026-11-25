"""Paper-only Coinbase Exchange New Listing + Volume Spike discovery (v2).

No order APIs, credentials, or token-launch predictions. New listing means an
Exchange USD pair first observed AFTER baseline, not a newly created token.
The normal paper engine controls all trade decisions, liquidity/spread checks,
position size, stops and simulated execution.
"""
from __future__ import annotations

import csv
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

SKIP = {'BTC','ETH','USDC','USDT','DAI','EURC','PYUSD','FDUSD','TUSD','WBTC'}
MAX_CHECK = 65  # maximum separate sniper candle lookups per cycle
FRESH_HOURS = 72
LOG_FIELDS = ['utc','pair','mode','score','hour_return','volume_ratio','hourly_usd','observed_hours']

def eligible(product):
    pid = str(product.get('id', ''))
    return (pid.endswith('-USD') and pid.count('-') == 1 and
            pid.split('-')[0] not in SKIP and
            product.get('status') == 'online' and
            not product.get('trading_disabled', False) and
            not product.get('cancel_only', False) and
            not product.get('limit_only', False))

def candles_short(session, pair, cb):
    raw = cb.get_json(session, f'/products/{pair}/candles', {'granularity': 900})
    if not isinstance(raw, list):
        return []
    rows = []
    for x in raw:
        if not isinstance(x, list) or len(x) < 6:
            continue
        try:
            row = {'t': int(x[0]), 'low': float(x[1]), 'high': float(x[2]),
                   'close': float(x[4]), 'volume': float(x[5])}
        except (ValueError, TypeError, OverflowError):
            continue
        if row['close'] > 0 and row['volume'] >= 0 and all(math.isfinite(row[k]) for k in ('low','high','close','volume')):
            rows.append(row)
    rows.sort(key=lambda r: r['t'])
    now = time.time()
    completed = [r for r in rows if r['t'] + 900 <= now]
    return completed if completed and completed[-1]['t'] + 900 >= now - 3600 else []

def _metrics(rows, now):
    if len(rows) < 9 or rows[-1]['t'] < now - 3600:
        return None
    # Explicitly reject broken latest 2h series; sparse history can be misleading.
    if any(b['t'] - a['t'] != 900 for a,b in zip(rows[-9:-1],rows[-8:])):
        return None
    p = [r['close'] for r in rows]
    hour = p[-1] / p[-5] - 1
    four = p[-1] / p[-9] - 1
    recent = sum(r['volume'] * r['close'] for r in rows[-4:])
    previous = sum(r['volume'] * r['close'] for r in rows[-8:-4])
    if previous <= 0 or not all(math.isfinite(x) for x in (hour, four, recent, previous)):
        return None
    return {'price': p[-1], 'hour': hour, 'four': four, 'vol_ratio': recent / previous,
            'vol_usd': recent, 'age_hours_est': (now - rows[0]['t']) / 3600}

def signal_recent(rows, now=None):
    """Historical short-history screen, which does NOT prove listing age."""
    now = time.time() if now is None else now
    f = _metrics(rows, now)
    if not f or f['age_hours_est'] > FRESH_HOURS:
        return None
    if not 0.025 <= f['hour'] <= 0.40 or f['vol_ratio'] < 1.6 or f['vol_usd'] < 20000:
        return None
    if f['price'] <= statistics.mean(r['close'] for r in rows[-5:]):
        return None
    f['score'] = round(min(100, 35 + min(30, f['hour'] * 100) + min(25, (f['vol_ratio'] - 1) * 9) + min(10, f['vol_usd'] / 20000)), 1)
    return f

def signal_spike(rows, now=None):
    """Established pairs: require meaningful momentum AND accelerating liquidity."""
    now = time.time() if now is None else now
    if len(rows) < 96:
        return None
    f = _metrics(rows, now)
    if not f or not 0.015 <= f['hour'] <= 0.40 or f['four'] <= 0:
        return None
    if f['vol_ratio'] < 1.4 or f['vol_usd'] < 30000:
        return None
    dollar_24h = sum(r['close'] * r['volume'] for r in rows[-96:])
    if dollar_24h < 250000:
        return None
    if f['price'] <= statistics.mean(r['close'] for r in rows[-5:]):
        return None
    f['vol_usd_24h'] = dollar_24h
    f['score'] = round(min(100, 30 + min(30, f['hour'] * 250) + min(25, (f['vol_ratio'] - 1) * 11) + min(15, f['four'] * 90)), 1)
    return f

def _log_signals(items):
    if not items:
        return
    # Independent diagnostic file. Does not modify orders, trade CSV, or balances.
    path = Path(__file__).resolve().parent / 'data' / 'sniper_signals_v2.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open('a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if needs_header:
            w.writeheader()
        for item in items:
            x = item['features']
            w.writerow({'utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                        'pair': item['pair'], 'mode': x['mode'], 'score': item['score'],
                        'hour_return': round(x['hour'], 6),
                        'volume_ratio': round(x['vol_ratio'], 3),
                        'hourly_usd': round(x['vol_usd'], 2),
                        'observed_hours': round(x.get('observed_hours', -1), 1)})

def scan_new_listings(session, state, cfg, cb, universe):
    """Return same candidate schema consumed by competition.py; preserve state."""
    catalog = state.setdefault('sniper_catalog', {'initialized': False, 'known': []})
    try:
        products = cb.get_json(session, '/products')
        if not isinstance(products, list):
            raise ValueError('invalid Coinbase pair catalog')
        pairs = sorted({p['id'] for p in products if eligible(p)})
        if not pairs:
            raise ValueError('empty Coinbase pair catalog')
        previous = set(catalog.get('known', []))
        newly = sorted(set(pairs) - previous) if catalog.get('initialized') else []
        catalog['known'] = pairs
        catalog['initialized'] = True
        # Observation timestamps reflect our scanner's first sighting, NOT token creation.
        observations = catalog.setdefault('observed_since', {})
        now = time.time()
        for pair in newly:
            observations.setdefault(pair, now)
        # Keep observations only for presently available pairs.
        for pair in list(observations):
            if pair not in pairs:
                del observations[pair]
    except Exception as exc:
        print(f'  SNIPER WARN catalog unavailable: {type(exc).__name__}: {exc}', flush=True)
        return []
    # Prioritize newly seen pairs; rotate additional independent catalog pairs,
    # then sample the normal scanner's universe. Bound requests each cycle.
    slot = int(state.get('cycle', 0))
    extra_count = max(0, MAX_CHECK - 20)
    extra = [p for p in pairs if p not in set(newly)]
    offset = (slot * extra_count) % len(extra) if extra else 0
    rotation = (extra[offset:] + extra[:offset])[:extra_count]
    check = list(dict.fromkeys(newly[:20] + rotation + list(universe)))[:MAX_CHECK]
    signals = []
    recent_count = spike_count = 0
    for pair in check:
        try:
            rows = candles_short(session, pair, cb)
            if not rows:
                continue
            observed_h = (now - observations[pair])/3600 if pair in observations else None
            # Mode A: only pairs first observed since we started catalog tracking.
            # Also screen historical short candle histories but never claim proof of launch.
            recent = signal_recent(rows, now) if (observed_h is not None and observed_h <= FRESH_HOURS) else None
            if recent:
                recent['mode'] = 'new_observation'
                recent_count += 1
                f = recent
            else:
                f = signal_spike(rows, now)
                if f:
                    f['mode'] = 'volume_spike'
                    spike_count += 1
            if f:
                if observed_h is not None:
                    f['observed_hours'] = observed_h
                signals.append({'pair':pair, 'score':f.pop('score'), 'features':f})
        except Exception as exc:
            print(f'  SNIPER WARN {pair}: {type(exc).__name__}: {exc}', flush=True)
        time.sleep(.11)
    signals.sort(key=lambda x: x['score'], reverse=True)
    _log_signals(signals)
    print(f'  New Coin Sniper v2 examined {len(check)} pairs, {len(newly)} newly observed, '
          f'{recent_count} early-mode signals, {spike_count} volume-spike signals', flush=True)
    return signals
