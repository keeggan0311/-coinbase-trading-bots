"""Offline checks: no Coinbase or OpenAI connections."""
import tempfile
from pathlib import Path
import competition as c

def main():
    cfg=c.config()
    s=c.new_state(cfg)
    assert len(s['bots'])==4
    a=s['bots']['Momentum Hunter']; b=s['bots']['Breakout Trader']
    q={'bid':10.,'ask':10.02,'spread':.002}
    with tempfile.TemporaryDirectory() as d:
        c.DATA=Path(d);c.EVENTS=c.DATA/'trades.csv';c.SNAPSHOTS=c.DATA/'leaderboard.csv';c.SIGNALS=c.DATA/'signals.csv';c.DASH=c.DATA/'dashboard.html';c.STATE=c.DATA/'competition_state.json'
        assert c.enter('Momentum Hunter',a,'FAKE-USD',q,cfg,'offline test')
        assert len(a['positions'])==1 and len(b['positions'])==0
        before=a['cash'];assert before==950.
        q2={'bid':12.,'ask':12.02,'spread':.002}
        pnl=c.exit_position('Momentum Hunter',a,'FAKE-USD',q2,cfg,'offline test')
        assert pnl>0 and a['wins']==1 and b['wins']==0
        assert abs(a['cash']-(1000+pnl))<1e-8
        c.dashboard(s,cfg,'test',2,0)
        assert c.DASH.exists() and 'SIMULATED' in c.DASH.read_text()
        assert c.EVENTS.exists()
        c.save(s)
        assert c.STATE.exists()
    f={'hour':.04,'four':.09,'vol_ratio':3.,'price':12.,'ma':11.,'prev_high':11.5,'high_24':15.,'low_recent':11.,'vol_usd':1e6}
    assert c.signal('Momentum Hunter',f)>0
    assert c.signal('Breakout Trader',f)>0
    assert c.signal('Dip Buyer',f)>0
    assert c.signal('Hybrid AI',f)>0
    print('PASS: four independent accounts, paper fills/fees, signals, dashboard, persistence')
if __name__=='__main__': main()
