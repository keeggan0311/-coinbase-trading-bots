"""Offline dashboard regressions. Never contacts the exchange."""
import csv
import json
import tempfile
from pathlib import Path
import competition as c


def main():
    cfg=c.config()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        c.DATA=root
        c.STATE=root/'competition_state.json'
        c.EVENTS=root/'trades.csv'
        c.SNAPSHOTS=root/'leaderboard.csv'
        c.SIGNALS=root/'signals.csv'
        c.DASH=root/'dashboard.html'
        state=c.new_state(cfg)
        state['bots']['Momentum Hunter']['last_equity']=1031.25
        state['bots']['Momentum Hunter']['cash']=981.25
        state['bots']['Momentum Hunter']['positions']={'FAKE-USD': {'cost':50,'qty':5,'last_bid':10.15,'entry':10}}
        state['bots']['Dip Buyer']['last_equity']=985.0
        state['bots']['Dip Buyer']['realized_pnl']=-15
        c.save(state)
        for name in c.NAMES:
            c.csv_append(c.SNAPSHOTS, {'utc':'2026-11-24T00:00:00Z','cycle':1,'bot':name,'equity':1000,'cash':1000})
            c.csv_append(c.SNAPSHOTS, {'utc':'2026-11-24T00:02:00Z','cycle':2,'bot':name,
                                       'equity':state['bots'][name]['last_equity'],'cash':state['bots'][name]['cash']})
        c.csv_append(c.EVENTS, {'utc':'2026-11-24T00:00:30Z','bot':'Momentum Hunter','action':'BUY','pair':'FAKE-USD','qty':5,'price':10,'fee':0.3,'net_pnl':'','reason':'test'})
        c.csv_append(c.EVENTS, {'utc':'2026-11-24T00:04:30Z','bot':'Dip Buyer','action':'SELL','pair':'MOCK-USD','qty':2,'price':9,'fee':0.1,'net_pnl':'-15','reason':'test'})
        c.dashboard(state,cfg,'AI OFF',50,0)
        html=c.DASH.read_text()
        assert 'Equity history' in html and '<svg' in html
        assert 'All on Black' in html and 'New Coin Sniper' in html
        assert '$1,031.25' in html and '$985.00' in html
        assert 'FAKE-USD' in html and 'MOCK-USD' in html
        assert 'Recent simulated trades' in html
        assert 'PAPER TRADING' in html and 'AI inactive' in html
        assert len(state['bots'])==6
        assert json.loads(c.STATE.read_text())['bots']['Momentum Hunter']['last_equity']==1031.25
        # No snapshots: still renders valid output.
        c.SNAPSHOTS.unlink()
        c.dashboard(state,cfg,'status only')
        assert 'No historical snapshots yet' in c.DASH.read_text()
    print('PASS: mobile HTML, charts, accounts, positions, trades, history fallback, state untouched')

if __name__=='__main__':main()
