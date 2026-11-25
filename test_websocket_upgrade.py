"""Offline tests. No network, no real orders."""
from __future__ import annotations
import copy
import json
import tempfile
from pathlib import Path
import competition as c
import ws_monitor as w


def run():
    assert w.ticker_quote({'type':'ticker','best_bid':'1.07','best_ask':'1.08'})['bid']==1.07
    assert w.ticker_quote({'type':'ticker','price':'1.50'}) is None
    assert w.ticker_quote({'type':'ticker','best_bid':'1.08','best_ask':'1.07'}) is None
    assert w.ticker_quote({'type':'heartbeat','best_bid':'1','best_ask':'1.01'}) is None

    cfg=json.loads((Path(__file__).parent/'config.json').read_text())
    import all_on_black as black
    black.STOP_LOSS=.15
    black.TRAILING_ACTIVATION=.06
    black.TRAILING_DISTANCE=.04
    with tempfile.TemporaryDirectory() as td:
        old=(c.DATA,c.STATE,c.EVENTS,c.SNAPSHOTS,c.SIGNALS,c.DASH)
        c.DATA=Path(td); c.STATE=c.DATA/'competition_state.json';c.EVENTS=c.DATA/'trades.csv';c.SNAPSHOTS=c.DATA/'leaderboard.csv';c.SIGNALS=c.DATA/'signals.csv';c.DASH=c.DATA/'dashboard.html'
        try:
            st=c.new_state(cfg)
            before=copy.deepcopy(st['bots']['Momentum Hunter'])
            b=st['bots']['All on Black']
            q={'bid':1.0,'ask':1.01,'spread':.00995}
            assert c.enter('All on Black',b,'TEST-USD',q,cfg,'unit test')
            assert b['cash']==0 and len(b['positions'])==1
            c.save(st)
            captured=[]
            original_monitor=c.PaperPriceMonitor
            original_dash=c.dashboard
            class Stub:
                def __init__(self,pairs,callback,**kw):
                    self.pairs=pairs;self.callback=callback;captured.append(self)
                def start(self):pass
                def stop(self):pass
            c.PaperPriceMonitor=Stub
            c.dashboard=lambda *a,**kw:None
            try:
                mon=c.start_ws_monitor(st,cfg)
                assert mon.pairs()==['TEST-USD']
                mon.callback('TEST-USD',{'bid':1.15,'ask':1.16,'spread':.00865})
                assert b['positions']['TEST-USD']['highest_bid']==1.15
                # >6% peak, then >4% retracement, sell exactly once.
                mon.callback('TEST-USD',{'bid':1.09,'ask':1.10,'spread':.00913})
                assert not b['positions'] and b['wins']==1
                assert mon.pairs()==[]
                assert st['bots']['Momentum Hunter']==before
                line_count=len(c.EVENTS.read_text().splitlines())
                mon.callback('TEST-USD',{'bid':.1,'ask':.11,'spread':.095})
                assert len(c.EVENTS.read_text().splitlines())==line_count
                reloaded=json.loads(c.STATE.read_text())
                assert reloaded['bots']['All on Black']['wins']==1
                assert reloaded['bots']['Momentum Hunter']==before
            finally:
                c.PaperPriceMonitor=original_monitor
                c.dashboard=original_dash
        finally:
            c.DATA,c.STATE,c.EVENTS,c.SNAPSHOTS,c.SIGNALS,c.DASH=old
    print('PASS: ticker filtering, trailing exit, one sell only, original accounts preserved, atomic persistence')

if __name__=='__main__':run()
