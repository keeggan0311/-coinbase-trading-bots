"""Six Coinbase public-data paper traders. NO order or authentication endpoints."""
from __future__ import annotations
import argparse
import csv
import html
import json
import math
import os
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
import market_base as cb
import new_listing_sniper as sniper
import all_on_black as black
from ws_monitor import PaperPriceMonitor

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
STATE = DATA / 'competition_state.json'
EVENTS = DATA / 'trades.csv'
SNAPSHOTS = DATA / 'leaderboard.csv'
SIGNALS = DATA / 'signals.csv'
DASH = DATA / 'dashboard.html'
NAMES = ['Momentum Hunter', 'Breakout Trader', 'Dip Buyer', 'Hybrid AI', 'New Coin Sniper', 'All on Black']
STATE_LOCK = threading.RLock()  # One state writer at a time: scanner or WebSocket thread


def config():
    load_dotenv(ROOT / '.env')
    cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    assert cfg['starting_balance'] > 0 and 0 < cfg['max_exposure'] <= cfg['starting_balance']
    assert cfg['position_usd'] > 0 and cfg['max_positions'] > 0
    assert 0 <= cfg['fee_rate'] < .1 and 0 <= cfg['slippage_rate'] < .1
    assert 0 < cfg['stop_loss'] < 1 and 0 < cfg['trailing_distance'] < 1
    assert cfg['loop_seconds'] >= 60 and 1 <= cfg['scan_limit'] <= 100
    return cfg


def new_state(cfg):
    return {'schema': 2, 'created': cb.utc_now(), 'cycle': 0, 'bots': {
        name: fresh_bot(cfg) for name in NAMES}, 'sniper_catalog': {'initialized': False, 'known': []}}

def fresh_bot(cfg):
    return {'cash': cfg['starting_balance'], 'positions': {}, 'cooldowns': {},
            'realized_pnl': 0, 'wins': 0, 'losses': 0, 'paused': False,
            'last_equity': cfg['starting_balance'], 'peak_equity': cfg['starting_balance'],
            'max_drawdown': 0, 'day': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'day_start_equity': cfg['starting_balance']}


def save(state):
    DATA.mkdir(exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2), encoding='utf-8')
    os.replace(tmp, STATE)


def load(cfg):
    DATA.mkdir(exist_ok=True)
    if not STATE.exists():
        st = new_state(cfg)
        save(st)
        return st
    st = json.loads(STATE.read_text(encoding='utf-8'))
    if st.get('schema') != 2 or not set(st['bots']).issubset(set(NAMES)):
        raise RuntimeError('Unrecognized state format; stop and inspect the backup')
    # Add only missing competitors; never alter prior balances, positions or history.
    for name in NAMES:
        if name not in st['bots']:
            st['bots'][name] = fresh_bot(cfg)
            print(f'Added paper account: {name} ($'+str(cfg['starting_balance'])+')')
    st.setdefault('sniper_catalog', {'initialized': False, 'known': []})
    save(st)
    return st


def csv_append(path, row):
    DATA.mkdir(exist_ok=True)
    with path.open('a', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if f.tell() == 0: w.writeheader()
        w.writerow(row)


def features(rows, cfg):
    if len(rows) < 96: return None
    p = [float(r['close']) for r in rows]
    v = [float(r['volume']) for r in rows]
    if min(p[-96:]) <= 0: return None
    last = p[-1]
    vol = sum(rows[-i]['close'] * rows[-i]['volume'] for i in range(1, 97))
    if vol < cfg['min_24h_usd_volume']: return None
    baseline = statistics.mean(v[-24:-4])
    vr = statistics.mean(v[-4:]) / baseline if baseline else 0
    hour = last / p[-5] - 1
    four = last / p[-17] - 1
    prev_high = max(r['high'] for r in rows[-21:-1])
    high_24 = max(r['high'] for r in rows[-96:-1])
    low_recent = min(r['low'] for r in rows[-9:-1])
    ma = statistics.mean(p[-20:])
    return {'price': last, 'vol_usd': vol, 'vol_ratio': vr,
            'hour': hour, 'four': four, 'ma': ma, 'prev_high': prev_high,
            'high_24': high_24, 'low_recent': low_recent,
            'ts': rows[-1]['t']}


def signal(strategy, f):
    h, four, vr, p = f['hour'], f['four'], f['vol_ratio'], f['price']
    if strategy in ('Momentum Hunter', 'Hybrid AI'):
        if not (.012 <= h <= .15 and four > 0 and vr >= 1.4 and p > f['ma']): return None
        return round(min(100, 30 + min(30,h*400) + min(20,(vr-1)*10) + min(20,four*150)),1)
    if strategy == 'Breakout Trader':
        if not (p > f['prev_high'] * 1.002 and h > .003 and vr >= 1.3 and four > 0): return None
        return round(min(100, 35 + min(30, (p/f['prev_high']-1)*750) + min(25,(vr-1)*14) + min(10,h*200)),1)
    if strategy == 'Dip Buyer':
        draw = 1 - f['low_recent']/f['high_24']
        rebound = p/f['low_recent'] - 1
        if not (.05 <= draw <= .45 and .008 <= rebound <= .12 and .002 <= h <= .085 and vr >= 1.1): return None
        return round(min(100, 30 + min(30,rebound*300) + min(25,(vr-1)*14) + min(15,draw*100)),1)
    if strategy == 'New Coin Sniper': return None  # separate short-history screen
    if strategy == 'All on Black': return black.signal(f)
    raise ValueError(strategy)


def quote(session, pair):
    # Deliberately separate from entry spread screen: open positions should be evaluated
    # for exit at the current bid even when the spread is wider than our entry limit.
    raw = cb.get_json(session, f'/products/{pair}/ticker')
    bid, ask = float(raw.get('bid') or 0), float(raw.get('ask') or 0)
    if bid <= 0 or ask <= 0 or ask < bid: raise ValueError('missing or crossed quote')
    return {'bid': bid, 'ask': ask, 'spread': (ask-bid)/((bid+ask)/2)}


def mark(bot, quotes, cfg):
    total = bot['cash']
    for pair, pos in bot['positions'].items():
        # Persist last observed quote; no silent use of historic peak for marking.
        q = quotes.get(pair)
        if q: pos['last_bid'] = q['bid']
        bid = pos.get('last_bid', pos['entry'])
        total += pos['qty'] * bid * (1-cfg['slippage_rate']) * (1-cfg['fee_rate'])
    return total


def log_trade(name, action, pair, qty, price, fee, pnl, reason):
    csv_append(EVENTS, {'utc': cb.utc_now(), 'bot': name, 'action': action, 'pair': pair,
                        'qty': round(qty,10), 'price': round(price,12), 'fee': round(fee,6),
                        'net_pnl': '' if pnl is None else round(pnl,6), 'reason': reason})


def enter(name, bot, pair, q, cfg, reason):
    if bot['paused'] or pair in bot['positions']: return False
    if name == 'All on Black':
        # Strictly paper-only, single concentrated position, fee-inclusive budget.
        if bot['positions']: return False
        budget = bot['cash']
    else:
        if len(bot['positions']) >= cfg['max_positions']: return False
        used = sum(pos['cost'] for pos in bot['positions'].values())
        budget = min(cfg['position_usd'], cfg['max_exposure']-used, bot['cash'])
    if budget < 10: return False
    cost_price = q['ask'] * (1+cfg['slippage_rate'])
    fee = budget * cfg['fee_rate']
    qty = (budget-fee)/cost_price
    if qty <= 0: return False
    bot['cash'] -= budget
    bot['positions'][pair] = {'qty':qty,'cost':budget,'entry':cost_price,'last_bid':q['bid'],
                              'highest_bid':q['bid'],'entered':cb.utc_now()}
    log_trade(name,'BUY',pair,qty,cost_price,fee,None,reason)
    return True


def exit_position(name,bot,pair,q,cfg,reason):
    pos = bot['positions'][pair]
    px = q['bid']*(1-cfg['slippage_rate'])
    proceeds = pos['qty']*px
    fee = proceeds*cfg['fee_rate']
    pnl = proceeds-fee-pos['cost']
    bot['cash'] += proceeds-fee
    bot['realized_pnl'] += pnl
    bot['wins' if pnl>0 else 'losses'] += 1
    bot['cooldowns'][pair] = time.time()
    del bot['positions'][pair]
    log_trade(name,'SELL',pair,pos['qty'],px,fee,pnl,reason)
    return pnl


def update_risk(name,bot,quotes,cfg):
    for pair in list(bot['positions']):
        if pair not in quotes:
            print(f'  WARN {name}: {pair} exit quote unavailable')
            continue
        q = quotes[pair]
        pos = bot['positions'][pair]
        pos['last_bid'] = q['bid']
        pos['highest_bid'] = max(pos['highest_bid'],q['bid'])
        net_current = pos['qty']*q['bid']*(1-cfg['slippage_rate'])*(1-cfg['fee_rate'])
        net_peak = pos['qty']*pos['highest_bid']*(1-cfg['slippage_rate'])*(1-cfg['fee_rate'])
        loss = (net_current/pos['cost'])-1
        peak = (net_peak/pos['cost'])-1
        stop = black.STOP_LOSS if name == 'All on Black' else cfg['stop_loss']
        activation = black.TRAILING_ACTIVATION if name == 'All on Black' else cfg['trailing_activation']
        distance = black.TRAILING_DISTANCE if name == 'All on Black' else cfg['trailing_distance']
        why = ('stop threshold' if loss <= -stop else
               'trailing profit' if peak >= activation and
                 q['bid'] <= pos['highest_bid']*(1-distance) else None)
        if why:
            pnl = exit_position(name,bot,pair,q,cfg,why)
            print(f'  PAPER SELL {name}: {pair} {pnl:+.2f} ({why})')


def check_pause(name, bot, total, cfg):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if bot['day'] != today:
        bot['day'] = today
        bot['day_start_equity'] = total
        # Do not automatically remove earlier safety pauses.
    bot['peak_equity'] = max(bot['peak_equity'],total)
    bot['max_drawdown'] = max(bot['max_drawdown'],1-total/bot['peak_equity'])
    bot['last_equity'] = total
    daily_limit = black.DAILY_LOSS_PAUSE if name == 'All on Black' else cfg['daily_loss_pause_usd']
    if bot['day_start_equity'] - total >= daily_limit:
        bot['paused'] = True


def ai_approval(items, cfg):
    # Unlike rules-only competitors, this lane never silently falls back to a fake AI.
    if not os.getenv('OPENAI_API_KEY'):
        return set(), 'AI OFF (configure OpenAI API key)'
    if not items: return set(), 'AI ON (no qualifying signals)'
    try:
        from openai import OpenAI
        client = OpenAI(timeout=20,max_retries=1)
        payload = [{'pair':x['pair'], 'score':x['score'],
                    'momentum_1h':round(x['features']['hour'],4),
                    'momentum_4h':round(x['features']['four'],4),
                    'volume_ratio':round(x['features']['vol_ratio'],2),
                    'usd_volume_24h':round(x['features']['vol_usd'])} for x in items]
        result = client.responses.create(
            model=os.getenv('OPENAI_MODEL',cfg['ai_model']),
            instructions=('Only assess the provided numeric crypto market observations. '
                          'Be skeptical of short-lived spikes and illiquid coins. '
                          'You may approve none. Respond with a JSON object having '
                          'only one key: approvals, which is an array of exact pair strings.'),
            input=json.dumps(payload), max_output_tokens=220)
        raw = result.output_text.strip()
        if raw.startswith('```'): raw=raw.split('\n',1)[-1].rsplit('```',1)[0].strip()
        obj=json.loads(raw)
        allow={x['pair'] for x in items}
        approvals=obj.get('approvals',[])
        if not isinstance(approvals,list): raise ValueError('AI approvals not list')
        return set(x for x in approvals if isinstance(x,str) and x in allow), 'AI ON'
    except Exception as e:
        print(f'  AI ERROR (entries blocked): {type(e).__name__}: {e}')
        return set(), 'AI ERROR (entries blocked)'


def dashboard(state,cfg,mode='unknown',last_scan=0,errors=0):
    """Render the phone-friendly dashboard from existing paper state and CSV history."""
    import dashboard_plus
    dashboard_plus.render(state, cfg, DATA, DASH, NAMES, mode, last_scan, errors)


def tick(state,session,cfg):
    # Share both candle history and ticker data so comparisons receive identical observations.
    universe=cb.products(session,cfg)
    rows_by_pair={}; features_by_pair={}; quotes={}; errors=0
    with STATE_LOCK:
        held=set().union(*(set(b['positions']) for b in state['bots'].values()))
    for pair in universe:
        try:
            rows=cb.candles(session,pair)
            f=features(rows,cfg)
            if f: features_by_pair[pair]=f
        except Exception as e:
            errors+=1;print(f'  candle warning {pair}: {e}')
        time.sleep(.11)
    # Get quotes for holdings regardless of whether they're in this scan rotation;
    # also quote entry candidates to avoid querying all 25 every loop.
    candidate={name:[] for name in NAMES}
    # Short-history screening is independent of the 96-candle legacy feature requirement.
    sniper_candidates = sniper.scan_new_listings(session, state, cfg, cb, universe)
    candidate['New Coin Sniper'] = sniper_candidates
    for pair,f in features_by_pair.items():
        for name in NAMES:
            if name == 'New Coin Sniper':
                continue
            score=signal(name,f)
            if score is not None:
                candidate[name].append({'pair':pair,'score':score,'features':f})
    for name in NAMES:
        candidate[name].sort(key=lambda x:x['score'],reverse=True)
    possible=set(held)
    for name in NAMES: possible.update(x['pair'] for x in candidate[name][:8])
    for pair in sorted(possible):
        try: quotes[pair]=quote(session,pair)
        except Exception as e: errors+=1;print(f'  quote warning {pair}: {e}')
        time.sleep(.11)
    approvals,mode=ai_approval(candidate['Hybrid AI'][:cfg['ai_shortlist']],cfg)
    with STATE_LOCK:
        # Mark accounts and close risk-triggered positions before allowing fresh entries.
        for name,b in state['bots'].items():
            update_risk(name,b,quotes,cfg)
            check_pause(name,b,mark(b,quotes,cfg),cfg)
        for name,b in state['bots'].items():
            for item in candidate[name][:1 if name == 'All on Black' else 8]:
                pair=item['pair'];q=quotes.get(pair)
                if b['paused'] or not q or q['spread'] > cfg['max_spread_pct']: continue
                if name=='Hybrid AI' and pair not in approvals: continue
                if pair in b['positions'] or time.time()-b['cooldowns'].get(pair,0)<cfg['cooldown_minutes']*60: continue
                if enter(name,b,pair,q,cfg,'AI-approved momentum' if name=='Hybrid AI' else 'rule signal'):
                    print(f'  PAPER BUY {name}: {pair} @ {q["ask"]:.8g}')
            check_pause(name,b,mark(b,quotes,cfg),cfg)
            csv_append(SNAPSHOTS,{'utc':cb.utc_now(),'cycle':state['cycle']+1,'bot':name,
                                  'equity':round(b['last_equity'],5),'cash':round(b['cash'],5),
                                  'positions':len(b['positions']),'realized_pnl':round(b['realized_pnl'],5),
                                  'wins':b['wins'],'losses':b['losses'],
                                  'max_drawdown':round(b['max_drawdown'],6),'paused':b['paused']})
        for name,items in candidate.items():
            for item in items[:8]:
                f=item['features']
                csv_append(SIGNALS,{'utc':cb.utc_now(),'cycle':state['cycle']+1,'bot':name,
                                    'pair':item['pair'],'score':item['score'],
                                    'hour_return':round(f['hour'],6),
                                    'volume_ratio':round(f['vol_ratio'],4),
                                    'usd_volume_24h':round(f['vol_usd'],2)})
        state['cycle']+=1
        save(state)
        dashboard(state,cfg,mode,len(universe),errors)
    print('\n  LEADERBOARD (simulated)')
    for name in sorted(NAMES,key=lambda n:state['bots'][n]['last_equity'],reverse=True):
        b=state['bots'][name]
        print(f'  {name:18} ${b["last_equity"]:9.2f} positions={len(b["positions"])} W/L={b["wins"]}/{b["losses"]}')
    print(f'  AI: {mode}. Scan: {len(universe)} pairs, warnings: {errors}')
    return True


def start_ws_monitor(state, cfg):
    """Start paper-only bid monitor in same process, sharing account state lock.

    A disconnected stream does not fabricate quotes; normal REST scans still
    check stop losses. Only the selected All on Black account is monitored.
    """
    last_written = [0.0]
    last_dashboard = [0.0]
    def pairs():
        with STATE_LOCK:
            return list(state['bots']['All on Black']['positions'])

    def on_quote(pair, q):
        with STATE_LOCK:
            b = state['bots']['All on Black']
            if pair not in b['positions']:
                return
            before = len(b['positions'])
            previous_peak = b['positions'][pair]['highest_bid']
            update_risk('All on Black', b, {pair:q}, cfg)
            total = mark(b, {pair:q}, cfg)
            check_pause('All on Black', b, total, cfg)
            exited = len(b['positions']) < before
            raised_peak = pair in b['positions'] and b['positions'][pair]['highest_bid'] > previous_peak
            now = time.monotonic()
            # Atomic file writes while holding lock; no duplicate entries or stale overwrite.
            if exited or raised_peak or now-last_written[0] >= 15:
                save(state)
                last_written[0] = now
            if exited or now-last_dashboard[0] >= 30:
                dashboard(state, cfg, 'AI OFF (WS monitoring)', cfg['scan_limit'], 0)
                last_dashboard[0] = now
            if exited:
                print(f'  WS PAPER exit confirmed for {pair}; account saved')

    monitor = PaperPriceMonitor(pairs, on_quote)
    monitor.start()
    return monitor


def main():
    p=argparse.ArgumentParser(description='Six Coinbase public-data PAPER bots')
    p.add_argument('--once',action='store_true')
    p.add_argument('--status',action='store_true')
    p.add_argument('--reset',action='store_true')
    a=p.parse_args()
    cfg=config()
    if a.reset:
        if STATE.exists():
            stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            backup=DATA/('competition_state_backup_'+stamp+'.json')
            STATE.rename(backup)
            print('Previous state preserved:',backup)
        for source in (EVENTS,SNAPSHOTS,SIGNALS,DASH):
            if source.exists(): source.rename(source.with_name(source.stem+'_backup_'+str(int(time.time()))+source.suffix))
    state=load(cfg)
    if a.status:
        dashboard(state,cfg,'status only')
        print('Dashboard: '+str(DASH))
        for name,b in state['bots'].items():
            print(f'{name}: ${b["last_equity"]:,.2f}, {len(b["positions"])} positions, paused={b["paused"]}')
        return
    print('PAPER TRADING ONLY. No exchange authentication. No real orders.')
    print('Dashboard: '+str(DASH))
    sess=requests.Session();sess.headers.update(cb.HEADERS)
    monitor = None if a.once else start_ws_monitor(state, cfg)
    try:
        while True:
            print('\n['+cb.utc_now()+'] Starting scan...')
            try: tick(state,sess,cfg)
            except Exception as e:
                print(f'Cycle error; inspect state: {type(e).__name__}: {e}')
                with STATE_LOCK:
                    save(state)
            if a.once: break
            time.sleep(cfg['loop_seconds'])
    except KeyboardInterrupt:
        with STATE_LOCK:
            save(state)
        print('Stopped; saved simulated balances')
    finally:
        if monitor is not None: monitor.stop()

if __name__=='__main__': main()
