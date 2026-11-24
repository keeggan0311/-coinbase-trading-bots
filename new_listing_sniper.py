"""Coinbase Exchange newly-trading-pair paper signal discovery; NEVER places orders.

This is NOT a token-launch sniper or a pump predictor.  "Recent" means the first
available completed 15m candle in the public Exchange window was <=48h ago.
Sparse history may make an older pair look new; screening cannot prove listing age.
"""
from __future__ import annotations
import statistics
import time

SKIP = {'BTC','ETH','USDC','USDT','DAI','EURC','PYUSD','FDUSD','TUSD','WBTC'}

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
    if not isinstance(raw, list): return []
    rows = sorted(({'t':int(x[0]),'low':float(x[1]), 'high':float(x[2]),
                   'close':float(x[4]),'volume':float(x[5])}
                   for x in raw if isinstance(x,list) and len(x)>=6),key=lambda r:r['t'])
    now=time.time()
    # Ignore forming candles and stale markets; last 15-minute data must be fresh.
    completed=[r for r in rows if r['t']+900 <= now]
    return completed if completed and completed[-1]['t']+900 >= now-3600 else []

def signal_recent(rows, now=None):
    now = now or time.time()
    # 2+ hours of 15m candles required; first candle is at most 48h ago.
    if len(rows)<9 or rows[0]['t'] < now-48*3600 or rows[-1]['t'] < now-3600: return None
    # Skip history with gaps that could hide an established but inactive pair.
    if any(b['t']-a['t']>1800 for a,b in zip(rows[-9:-1],rows[-8:])): return None
    p=[x['close'] for x in rows]
    if min(p)<=0: return None
    hour=p[-1]/p[-5]-1
    if not .025 <= hour <= .40: return None
    recent_volume=sum(x['volume']*x['close'] for x in rows[-4:])
    prev_volume=sum(x['volume']*x['close'] for x in rows[-8:-4])
    if prev_volume<=0 or recent_volume<20000: return None
    ratio=recent_volume/prev_volume
    if ratio < 1.6: return None
    if p[-1] <= statistics.mean(p[-5:]): return None
    score=round(min(100,35+min(30,hour*100)+min(25,(ratio-1)*9)+min(10,recent_volume/20000)),1)
    age_h=(now-rows[0]['t'])/3600
    return {'price':p[-1], 'hour':hour, 'four':p[-1]/p[-9]-1,
            'vol_ratio':ratio,'vol_usd':recent_volume,'age_hours_est':age_h,
            'score':score}

def scan_new_listings(session, state, cfg, cb, universe):
    # The Exchange /products endpoint has no authoritative token launch date.
    # Record newly appearing IDs after baseline, plus sample the normal rotating
    # 25-100 pair universe for short candle histories.
    catalog = state.setdefault('sniper_catalog', {'initialized':False,'known':[]})
    try:
        raw = cb.get_json(session, '/products')
        pairs = {p['id'] for p in raw if eligible(p)}
        if not pairs: raise ValueError('empty Coinbase pair catalog')
        previous=set(catalog.get('known',[]))
        newly_seen=sorted(pairs-previous) if catalog.get('initialized') else []
        catalog['known']=sorted(pairs)
        catalog['initialized']=True
    except Exception as exc:
        print(f'  SNIPER WARN catalog fetch failed; no entries: {exc}')
        return []
    # Deduplicate, prioritize newly appeared pairs and otherwise sample the
    # same rotation, bounded to avoid hammering Coinbase API.
    check=list(dict.fromkeys(newly_seen[:20]+list(universe)[:max(0,30-len(newly_seen[:20]))]))[:30]
    signals=[]
    for pair in check:
        try:
            rows=candles_short(session,pair,cb)
            f=signal_recent(rows)
            if f: signals.append({'pair':pair,'score':f.pop('score'),'features':f})
        except Exception as exc:
            print(f'  SNIPER WARN {pair}: {exc}')
        time.sleep(.11)
    print(f'  New Coin Sniper examined {len(check)} pairs, {len(newly_seen)} newly observed, {len(signals)} signals')
    return sorted(signals,key=lambda i:i['score'],reverse=True)
