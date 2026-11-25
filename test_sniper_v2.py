"""Offline regression test for New Coin Sniper v2. No network, no orders."""
import tempfile
import time
from unittest.mock import patch
import new_listing_sniper as sniper

NOW = 1795600000

def rows(n=300, mode='spike'):
    out=[]
    for i in range(n):
        # Last candle completed, continuous 15-minute history.
        t = NOW - (n-i)*900
        price = 1.0 + 0.0004*i
        volume = 3000.0
        if i >= n-4:
            price += .045 + .01*(i-(n-4))
            volume=25000.0
        elif i >= n-8:
            volume=5000.0
        out.append({'t':t, 'low':price*.99, 'high':price*1.01,
                    'close':price, 'volume':volume})
    return out

def main():
    established=rows()
    assert sniper.signal_spike(established, NOW), 'volume spike must qualify'
    assert sniper.signal_recent(established, NOW) is None, 'old history must not be recent'
    young=rows(18)
    assert sniper.signal_recent(young, NOW), 'fresh history should qualify'
    assert sniper.signal_spike(young, NOW) is None, 'short history must not qualify as established'
    gap=[dict(r) for r in established]
    gap[-3]['t']+=1800
    assert sniper.signal_spike(gap, NOW) is None, 'gapped candle data must be rejected'
    no_momentum=[dict(r) for r in established]
    for r in no_momentum:
        r['close']=1.0
    assert sniper.signal_spike(no_momentum, NOW) is None
    class CB:
        def get_json(self, session, path, params=None):
            if path=='/products':
                return [{'id':f'C{i:03d}-USD','status':'online'} for i in range(120)]
            return [[r['t'],r['low'],r['high'],r['close'],r['close'],r['volume']] for r in established]
    state={'cycle':0,'sniper_catalog':{'initialized':False,'known':[]}}
    # No writes outside temporary directory for unit test.
    with tempfile.TemporaryDirectory() as tmp, patch.object(sniper, '_log_signals'), patch.object(sniper.time,'time',return_value=NOW), patch.object(sniper.time,'sleep'):
        got=sniper.scan_new_listings(None,state,{},CB(),['C000-USD'])
        assert len(got)>0 and len(got)<=sniper.MAX_CHECK
        assert all(x['features']['mode']=='volume_spike' for x in got)
        assert not state['sniper_catalog']['observed_since'], 'baseline must not claim new tokens'
        state['cycle']=1
        got2=sniper.scan_new_listings(None,state,{},CB(),['C000-USD'])
        assert len(got2)>0
    print('PASS: two independent signal modes, historical filters, rotating discovery, no account changes')

if __name__ == '__main__':main()
