"""Offline multi-account WebSocket regression: no exchange connection or real orders."""
import copy
import csv
import json
import tempfile
import time
from pathlib import Path
import competition as c
import ws_monitor as ws
import all_on_black as black


def run():
    cfg=json.loads((Path(__file__).parent/'config.json').read_text())
    black.STOP_LOSS=.15
    black.TRAILING_ACTIVATION=.06
    black.TRAILING_DISTANCE=.04
    assert ws.ticker_quote({'type':'ticker','best_bid':'1.05','best_ask':'1.06'})['bid']==1.05
    assert ws.ticker_quote({'type':'ticker','best_bid':'1.06','best_ask':'1.05'}) is None
    with tempfile.TemporaryDirectory() as td:
        old=(c.DATA,c.STATE,c.EVENTS,c.SNAPSHOTS,c.SIGNALS,c.DASH)
        c.DATA=Path(td);c.STATE=c.DATA/'competition_state.json';c.EVENTS=c.DATA/'trades.csv';c.SNAPSHOTS=c.DATA/'leaderboard.csv';c.SIGNALS=c.DATA/'signals.csv';c.DASH=c.DATA/'dashboard.html'
        old_mon,old_dash=c.PaperPriceMonitor,c.dashboard
        c.WS_QUOTES.clear()
        try:
            st=c.new_state(cfg)
            ordinary=st['bots']['Momentum Hunter'];blackbot=st['bots']['All on Black']
            q={'bid':1.0,'ask':1.01,'spread':.00995}
            assert c.enter('Momentum Hunter',ordinary,'SAME-USD',q,cfg,'test')
            assert c.enter('All on Black',blackbot,'SAME-USD',q,cfg,'test')
            untouched=copy.deepcopy(st['bots']['Dip Buyer'])
            seen=[]
            class Stub:
                def __init__(self,pairs,on_quote,**kwargs):
                    self.pairs=pairs;self.on_quote=on_quote;seen.append(self)
                def start(self):pass
                def stop(self):pass
            c.PaperPriceMonitor=Stub;c.dashboard=lambda *args,**kw:None
            mon=c.start_ws_monitor(st,cfg)
            assert mon.pairs()==['SAME-USD']
            mon.on_quote('SAME-USD',{'bid':1.20,'ask':1.21,'spread':.0083})
            assert ordinary['positions']['SAME-USD']['highest_bid']==1.2
            assert blackbot['positions']['SAME-USD']['highest_bid']==1.2
            # All on Black trails by 4%, others by 7%. Only All on Black exits.
            mon.on_quote('SAME-USD',{'bid':1.14,'ask':1.15,'spread':.0087})
            assert len(blackbot['positions'])==0 and blackbot['wins']==1
            assert 'SAME-USD' in ordinary['positions']
            # After a greater fall, other bot also exits, exactly once.
            mon.on_quote('SAME-USD',{'bid':1.10,'ask':1.11,'spread':.009})
            assert len(ordinary['positions'])==0 and ordinary['wins']==1
            assert mon.pairs()==[] and st['bots']['Dip Buyer']==untouched
            with c.EVENTS.open(newline='') as f: rows=list(csv.DictReader(f))
            sells=[r for r in rows if r['action']=='SELL']
            assert len(sells)==2 and all(r['reason'].startswith('WS trailing profit') for r in sells)
            assert 'SAME-USD' not in c.WS_QUOTES
            mon.on_quote('SAME-USD',{'bid':.1,'ask':.11,'spread':.095})
            with c.EVENTS.open(newline='') as f: assert len([r for r in csv.DictReader(f) if r['action']=='SELL'])==2
            saved=json.loads(c.STATE.read_text());assert saved['bots']['Dip Buyer']==untouched
            # Quote cache is fresher than an HTTP value fetched before live update.
            assert 'WS_QUOTES' in c.tick.__code__.co_names
        finally:
            c.PaperPriceMonitor=old_mon;c.dashboard=old_dash
            c.DATA,c.STATE,c.EVENTS,c.SNAPSHOTS,c.SIGNALS,c.DASH=old
            c.WS_QUOTES.clear()
    print('PASS: two bots one subscription, independent trailing rules, exact-once exits, paper history preserved')

if __name__=='__main__':run()
