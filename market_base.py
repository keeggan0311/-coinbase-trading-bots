"""Coinbase public-data PAPER trader. This code contains no real-order functionality."""
from __future__ import annotations
import argparse
import csv
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE = 'https://api.exchange.coinbase.com'
ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
STATE = DATA / 'paper_state.json'
TRADES = DATA / 'trades.csv'
LOG = DATA / 'scan_log.csv'
HEADERS = {'User-Agent': 'CoinbasePaperMomentumResearch/1.0', 'Accept': 'application/json'}
DEFAULTS = dict(starting_balance=1000.0, max_exposure=200.0, position_usd=50.0,
                max_positions=4, fee_rate=0.006, slippage_rate=0.002, stop_loss=0.15,
                trailing_activation=0.12, trailing_distance=0.07, cooldown_minutes=45,
                min_24h_usd_volume=250000.0, min_momentum_1h=0.012,
                max_momentum_1h=0.15, min_volume_ratio=1.4,
                max_spread_pct=0.012, loop_seconds=300, scan_limit=25,
                ai_shortlist=4, ai_min_score=65, ai_model='gpt-4.1-mini')

def utc_now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def load_config():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    path = ROOT / 'config.json'
    cfg = dict(DEFAULTS)
    if path.exists(): cfg.update(json.loads(path.read_text(encoding='utf-8')))
    if cfg['position_usd'] <= 0 or cfg['max_exposure'] <= 0 or cfg['starting_balance'] <= 0:
        raise ValueError('Capital settings must be positive')
    if not 0 <= cfg['fee_rate'] < 0.1 or not 0 <= cfg['slippage_rate'] < 0.1:
        raise ValueError('Invalid fee/slippage setting')
    return cfg

def get_json(session, path, params=None):
    for attempt in range(3):
        try:
            r = session.get(BASE + path, params=params, timeout=15)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == 2: raise RuntimeError(f'Coinbase public data unavailable: {path}: {e}') from e
            time.sleep(1 + attempt)
    raise RuntimeError('Coinbase API rate limit/server failure for ' + path)

def init_state(cfg):
    DATA.mkdir(exist_ok=True)
    if STATE.exists():
        saved = json.loads(STATE.read_text(encoding='utf-8'))
        if saved.get('schema') != 1: raise RuntimeError('Unknown state file version')
        return saved
    state = {'schema': 1, 'cash': float(cfg['starting_balance']), 'positions': {},
             'cooldowns': {}, 'created': utc_now(), 'paused': False, 'realized_pnl': 0.0,
             'day': datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'day_start_equity': float(cfg['starting_balance'])}
    save_state(state)
    return state

def save_state(state):
    DATA.mkdir(exist_ok=True)
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2), encoding='utf-8')
    os.replace(temp, STATE)

def write_csv(path, columns, row):
    DATA.mkdir(exist_ok=True)
    is_new = not path.exists()
    with path.open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        if is_new: writer.writeheader()
        writer.writerow(row)

def products(session, cfg):
    listing = get_json(session, '/products')
    if not isinstance(listing, list): raise RuntimeError('Unexpected products format')
    skip = {'BTC','ETH','USDC','USDT','DAI','EURC','PYUSD','FDUSD','TUSD','WBTC'}
    result = []
    for p in listing:
        pid = str(p.get('id', ''))
        asset = pid.split('-')[0]
        if (pid.endswith('-USD') and pid.count('-') == 1
                and p.get('status') == 'online' and not p.get('trading_disabled', False)
                and not p.get('cancel_only', False) and not p.get('limit_only', False)
                and asset not in skip):
            result.append(pid)
    # Rotate through candidates across cycles; stable hash-free deterministic selection.
    result.sort()
    if not result: raise RuntimeError('No eligible Coinbase USD products returned')
    slice_size = max(1, int(cfg['scan_limit']))
    slot = int(time.time() // max(60, cfg['loop_seconds']))
    offset = (slot * slice_size) % len(result)
    rotated = result[offset:] + result[:offset]
    return rotated[:slice_size]

def candles(session, product):
    raw = get_json(session, f'/products/{product}/candles', {'granularity': 900})
    if not isinstance(raw, list): raise ValueError('Unexpected candle format')
    # Exchange response: [time, low, high, open, close, volume]
    rows = sorted(({'t': int(x[0]), 'low': float(x[1]), 'high': float(x[2]),
                    'open': float(x[3]), 'close': float(x[4]), 'volume': float(x[5])}
                   for x in raw if isinstance(x, list) and len(x) >= 6), key=lambda x:x['t'])
    # Remove the currently forming 15-minute candle to avoid look-ahead and noisy signals.
    completed = [x for x in rows if x['t'] + 900 <= time.time()]
    if len(completed) < 26: return []
    if time.time() - completed[-1]['t'] > 3600: return []
    return completed

def evaluate(rows, cfg):
    if len(rows) < 26: return None
    last = rows[-1]['close']
    if last <= 0: return None
    one_hour = last / rows[-5]['close'] - 1
    four_hour = last / rows[-17]['close'] - 1
    recent = statistics.mean(r['volume'] for r in rows[-4:])
    prior = statistics.mean(r['volume'] for r in rows[-24:-4])
    ratio = recent / prior if prior > 0 else 0
    approx_24h_volume_usd = sum(r['close'] * r['volume'] for r in rows[-96:])
    # Require ~24h of candles for accurate 24h liquidity screening.
    if len(rows) < 96: return None
    if approx_24h_volume_usd < cfg['min_24h_usd_volume']: return None
    if not cfg['min_momentum_1h'] <= one_hour <= cfg['max_momentum_1h']: return None
    if four_hour <= 0 or ratio < cfg['min_volume_ratio']: return None
    closes = [r['close'] for r in rows[-20:]]
    ma = statistics.mean(closes)
    if last <= ma: return None
    score = min(100, 30 + min(30, one_hour * 400) + min(20, (ratio - 1) * 10) + min(20, four_hour * 150))
    return {'price': last, 'momentum_1h': one_hour, 'momentum_4h': four_hour,
            'volume_ratio': ratio, 'usd_volume_24h': approx_24h_volume_usd,
            'score': round(score, 1), 'candle_ts': rows[-1]['t']}

def ticker_check(session, product, cfg):
    t = get_json(session, f'/products/{product}/ticker')
    bid, ask = float(t.get('bid') or 0), float(t.get('ask') or 0)
    if bid <= 0 or ask <= 0 or ask < bid: return None
    spread = (ask-bid)/((ask+bid)/2)
    if spread > cfg['max_spread_pct']: return None
    return {'bid': bid, 'ask': ask, 'spread': spread}

def ai_review(candidates, cfg):
    """Optional genuine LLM reviewer. Fails closed (no AI approvals) if API fails."""
    if not os.getenv('OPENAI_API_KEY'):
        return None, 'AI OFF (no OPENAI_API_KEY)'
    try:
        from openai import OpenAI
        client = OpenAI(timeout=20, max_retries=1)
        payload = [{'pair': c['pair'], 'momentum_1h': round(c['momentum_1h'],4),
                    'momentum_4h': round(c['momentum_4h'],4),
                    'volume_ratio': round(c['volume_ratio'],2),
                    'usd_volume_24h': round(c['usd_volume_24h'])} for c in candidates]
        response = client.responses.create(model=os.getenv('OPENAI_MODEL', cfg['ai_model']),
            instructions=('You are a cautious reviewer of speculative cryptocurrency momentum signals. '
                          'Consider ONLY supplied metrics, not unverified news or future predictions. '
                          'Return JSON containing approvals array of product IDs. '
                          'Approve only if volume and momentum look sustainable relative to peers. '
                          'It is fine to approve none. No text outside JSON.'),
            input=json.dumps(payload), max_output_tokens=250)
        text = response.output_text.strip()
        if text.startswith('```'):
            text = text.split('\n',1)[-1].rsplit('```',1)[0].strip()
        result = json.loads(text)
        requested = result.get('approvals', [])
        valid = {c['pair'] for c in candidates}
        approved = {x for x in requested if isinstance(x, str) and x in valid}
        return approved, 'AI ON'
    except Exception as e:
        print(f'[AI] reviewer failed; NO NEW AI-REVIEWED ENTRIES: {type(e).__name__}: {e}')
        return set(), 'AI ERROR (entries blocked)'

def buy(state, pair, market, cfg):
    if state['paused'] or pair in state['positions']: return False
    if len(state['positions']) >= cfg['max_positions']: return False
    exposed = sum(p['cost'] for p in state['positions'].values())
    budget = min(cfg['position_usd'], cfg['max_exposure'] - exposed, state['cash'])
    if budget < 10: return False
    execution_price = market['ask'] * (1+cfg['slippage_rate'])
    fee = budget * cfg['fee_rate']
    qty = (budget - fee) / execution_price
    if qty <= 0: return False
    state['cash'] = round(state['cash']-budget, 8)
    state['positions'][pair] = {'qty': qty, 'entry': execution_price, 'cost': budget,
                                  'highest_bid': market['bid'], 'time': utc_now(),
                                  'candle_ts': market.get('candle_ts')}
    write_csv(TRADES, ['time','action','pair','qty','price','gross','fee','pnl','reason'],
              dict(time=utc_now(),action='BUY',pair=pair,qty=qty,price=execution_price,
                   gross=budget,fee=fee,pnl='',reason='momentum+AI' if os.getenv('OPENAI_API_KEY') else 'momentum'))
    print(f'PAPER BUY {pair}: ${budget:.2f} @ {execution_price:.8g}')
    return True

def sell(state, pair, market, cfg, reason):
    p = state['positions'][pair]
    execution_price = market['bid'] * (1-cfg['slippage_rate'])
    proceeds = p['qty'] * execution_price
    fee = proceeds * cfg['fee_rate']
    net = proceeds - fee
    pnl = net - p['cost']
    state['cash'] = round(state['cash'] + net, 8)
    state['realized_pnl'] = round(state['realized_pnl'] + pnl, 8)
    state['cooldowns'][pair] = time.time()
    del state['positions'][pair]
    write_csv(TRADES, ['time','action','pair','qty','price','gross','fee','pnl','reason'],
              dict(time=utc_now(),action='SELL',pair=pair,qty=p['qty'],price=execution_price,
                   gross=proceeds,fee=fee,pnl=pnl,reason=reason))
    print(f'PAPER SELL {pair}: {reason}, P/L ${pnl:+.2f}')

def exits(state, session, cfg, prices):
    for pair in list(state['positions']):
        try:
            quote = ticker_check(session, pair, cfg)
            if quote is None:
                # Wide spreads can prevent exits; log and retry next cycle.
                print(f'WARNING: {pair} no valid/tight quote; exit not filled')
                continue
            prices[pair] = quote['bid']
            p = state['positions'][pair]
            p['highest_bid'] = max(p['highest_bid'], quote['bid'])
            net_bid = quote['bid'] * (1-cfg['slippage_rate']) * (1-cfg['fee_rate'])
            gain = (net_bid*p['qty']/p['cost']) - 1
            peak_gain = (p['highest_bid'] * (1-cfg['slippage_rate']) * (1-cfg['fee_rate']) * p['qty']/p['cost']) - 1
            if gain <= -cfg['stop_loss']:
                sell(state, pair, quote, cfg, 'stop-loss attempt')
            elif peak_gain >= cfg['trailing_activation'] and quote['bid'] <= p['highest_bid'] * (1-cfg['trailing_distance']):
                sell(state, pair, quote, cfg, 'trailing profit exit')
            time.sleep(.15)
        except Exception as e:
            print(f'WARNING: could not evaluate exit {pair}: {e}')

def equity(state, prices, cfg):
    # Price each position at an estimated executable exit, or last known bid fallback.
    return state['cash'] + sum(p['qty'] * prices.get(pair, p['highest_bid']) *
            (1-cfg['slippage_rate']) * (1-cfg['fee_rate']) for pair,p in state['positions'].items())

def cycle(state, session, cfg, allow_entries=True):
    prices = {}
    exits(state, session, cfg, prices)
    total = equity(state, prices, cfg)
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if state['day'] != day:
        state['day'], state['day_start_equity'] = day, total
    # Daily loss limit based on initial simulated active allocation.
    loss_limit = min(30.0, 0.15 * cfg['max_exposure'])
    if state['day_start_equity'] - total >= loss_limit:
        if not state['paused']: print(f'PAUSED: daily account loss >= ${loss_limit:.2f}')
        state['paused'] = True
    print(f'[{utc_now()}] cash ${state["cash"]:.2f} | estimated equity ${total:.2f} | positions {len(state["positions"])} | paused={state["paused"]}')
    save_state(state)
    if state['paused'] or not allow_entries: return
    matches = []
    universe = products(session, cfg)
    for idx, pair in enumerate(universe, 1):
        try:
            history = candles(session, pair)
            signal = evaluate(history, cfg)
            if signal is not None:
                signal['pair'] = pair
                matches.append(signal)
        except Exception as e:
            print(f'  skipped {pair}: {type(e).__name__}: {e}')
        time.sleep(.15)
    matches.sort(key=lambda x:x['score'], reverse=True)
    shortlist = matches[:cfg['ai_shortlist']]
    approvals, mode = ai_review(shortlist, cfg)
    print(f'  scanned {len(universe)} Coinbase pairs; {len(matches)} signals; {mode}')
    for c in matches[:8]:
        write_csv(LOG, ['time','pair','score','momentum_1h','momentum_4h','volume_ratio','usd_volume_24h','ai_status'],
                  dict(time=utc_now(),**c,ai_status=mode))
        print(f'  {c["pair"]:14} score {c["score"]:5.1f}  1h {c["momentum_1h"]:+.1%}  vol x{c["volume_ratio"]:.1f}')
    for c in shortlist:
        if c['pair'] in state['positions'] or c['score'] < cfg['ai_min_score']: continue
        if approvals is not None and c['pair'] not in approvals: continue
        if time.time() - state['cooldowns'].get(c['pair'], 0) < cfg['cooldown_minutes']*60: continue
        try:
            q = ticker_check(session, c['pair'], cfg)
            if q is not None: buy(state, c['pair'], {**c, **q}, cfg)
        except Exception as e:
            print(f'  quote skipped {c["pair"]}: {e}')
        time.sleep(.15)
    save_state(state)

def main():
    parser = argparse.ArgumentParser(description='PUBLIC DATA ONLY; PAPER TRADING ONLY')
    parser.add_argument('--once', action='store_true', help='Run one full scan then quit')
    parser.add_argument('--status', action='store_true', help='Show saved simulation state')
    parser.add_argument('--reset', action='store_true', help='Reset simulated account (backs up old state)')
    args = parser.parse_args()
    cfg = load_config()
    DATA.mkdir(exist_ok=True)
    if args.reset and STATE.exists():
        backup = DATA / ('paper_state_backup_' + str(int(time.time())) + '.json')
        STATE.rename(backup)
        print('Previous simulated state backed up:', backup)
    state = init_state(cfg)
    if args.status:
        print(json.dumps(state, indent=2))
        return
    print('PAPER MODE ONLY. NO COINBASE LOGIN. NO REAL ORDERS.')
    print('Ctrl+C to stop. Review data/trades.csv for simulated fills.')
    session = requests.Session()
    session.headers.update(HEADERS)
    while True:
        try:
            cycle(state, session, cfg)
        except Exception as e:
            print(f'ERROR: cycle failed, no new orders attempted: {type(e).__name__}: {e}')
            save_state(state)
        if args.once: break
        try: time.sleep(cfg['loop_seconds'])
        except KeyboardInterrupt:
            print('\nStopped. Simulation state saved.')
            break

if __name__ == '__main__': main()
