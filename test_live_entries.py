"""Offline-only live-entry tests. Does not contact Coinbase or place orders."""
import copy
import csv
import json
import tempfile
from pathlib import Path
import competition as c
import ws_monitor
from live_entry import LiveEntryTracker


def test_tracker():
    t=LiveEntryTracker()
    assert t.observe('TEST-USD',{'bid':1,'ask':1.001,'trade_price':1,'last_size':10,'trade_id':1},now=0) is None
    assert t.observe('TEST-USD',{'bid':1,'ask':1.001,'trade_price':1,'last_size':10,'trade_id':1},now=1) is None
    # Ticker stream observed for more than 3 minutes, rising volume and price.
    m=None
    for i in range(1,242):
        p=1+i*.0004
        size=100 if i<180 else 350
        q={'bid':p,'ask':p*1.001,'spread':.000999,'trade_price':p,
           'last_size':size,'trade_id':i+1}
        m=t.observe('TEST-USD',q,now=i)
    assert m and m['m1'] > .012 and m['m3'] > .03 and m['volume_ratio'] > 2
    assert t.select('TEST-USD',q,m,'New Coin Sniper',now=241)
    assert t.select('TEST-USD',q,m,'All on Black',now=241)
    assert not t.select('TEST-USD',q,m,'All on Black',now=242)  # no repeated tick buys
    t.reset()
    assert not t.ticks and not t.started
    # Reconnecting does not reuse accumulated signals
    assert t.observe('TEST-USD',q,now=1000) is None


def test_integration():
    cfg=json.loads((Path(__file__).parent/'config.json').read_text())
    with tempfile.TemporaryDirectory() as td:
        old=(c.DATA,c.STATE,c.EVENTS,c.SNAPSHOTS,c.SIGNALS,c.DASH)
        old_mon,old_dash=c.PaperPriceMonitor,c.dashboard
        try:
            c.DATA=Path(td); c.STATE=c.DATA/'competition_state.json'
            c.EVENTS=c.DATA/'trades.csv';c.SNAPSHOTS=c.DATA/'leaderboard.csv'
            c.SIGNALS=c.DATA/'signals.csv';c.DASH=c.DATA/'dashboard.html'
            st=c.new_state(cfg)
            untouched=copy.deepcopy(st['bots']['Dip Buyer'])
            c.LIVE_ENTRY_TRACKER.reset();c.LIVE_ENTRY_WATCHLIST.clear()
            c.LIVE_ENTRY_WATCHLIST.add('TEST-USD');c.WS_QUOTES.clear();c.LIVE_LAST_BUY.clear()
            created=[]
            class Stub:
                def __init__(self,pairs,on_quote,**kwargs):
                    self.pairs=pairs;self.on_quote=on_quote;self.on_reconnect=kwargs['on_reconnect'];created.append(self)
                def start(self):pass
                def stop(self):pass
            c.PaperPriceMonitor=Stub;c.dashboard=lambda *a,**kw:None
            mon=c.start_ws_monitor(st,cfg)
            assert 'TEST-USD' in mon.pairs()
            # Override monotonic timing via observe wrapping to simulate 241 seconds
            tracker=c.LIVE_ENTRY_TRACKER
            original_observe=tracker.observe
            index=[-1]
            def clocked_observe(pair,q,now=None):
                index[0]+=1
                return original_observe(pair,q,now=index[0])
            tracker.observe=clocked_observe
            # For the integration, pin select's clock to synthetic time as well.
            original_select=tracker.select
            tracker.select=lambda pair,q,metrics,name,now=None:original_select(pair,q,metrics,name,now=index[0])
            for i in range(242):
                p=1+i*.0004
                q={'bid':p,'ask':p*1.001,'spread':.000999,'trade_price':p,
                   'last_size':100 if i<180 else 350,'trade_id':i+1}
                mon.on_quote('TEST-USD',q)
            assert 'TEST-USD' in st['bots']['New Coin Sniper']['positions']
            assert 'TEST-USD' in st['bots']['All on Black']['positions']
            assert st['bots']['Dip Buyer']==untouched
            assert len(st['bots']['All on Black']['positions'])==1
            with c.EVENTS.open(newline='') as f:
                buys=[r for r in csv.DictReader(f) if r['action']=='BUY']
            assert len(buys)==2
            assert all('WS live momentum' in r['reason'] for r in buys)
            with c.EVENTS.open(newline='') as f:
                assert len([r for r in csv.DictReader(f) if r['action']=='BUY'])==2
            assert st['bots']['All on Black']['cash'] < 1e-6
            saved=json.loads(c.STATE.read_text())
            assert saved['bots']['Dip Buyer']==untouched
        finally:
            c.PaperPriceMonitor=old_mon;c.dashboard=old_dash
            c.DATA,c.STATE,c.EVENTS,c.SNAPSHOTS,c.SIGNALS,c.DASH=old
            c.LIVE_ENTRY_WATCHLIST.clear(); c.WS_QUOTES.clear();c.LIVE_LAST_BUY.clear()
            c.LIVE_ENTRY_TRACKER=LiveEntryTracker()

if __name__=='__main__':
    test_tracker(); test_integration()
    print('PASS: warmup, dedup, live buy limits, two independent bots, no real orders, history preserved')
