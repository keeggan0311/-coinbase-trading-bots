"""Static, mobile-first dashboard for Coinbase PAPER competition.

Only reads saved simulation records and writes dashboard.html. Has no exchange calls,
account access, network activity, executable JS, or API key handling.
"""
from __future__ import annotations
import csv
import html
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

COLORS = ['#64e3cd', '#83a8ff', '#f9c36b', '#bb91ff', '#63d4fa', '#ff8194']
SHORT = {'Momentum Hunter':'Momentum', 'Breakout Trader':'Breakout',
         'Dip Buyer':'Dip Buyer', 'Hybrid AI':'Hybrid AI',
         'New Coin Sniper':'New Coin', 'All on Black':'All on Black'}


def h(v):
    return html.escape(str(v), quote=True)


def num(v, default=0.0):
    try:
        f=float(v)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError, OverflowError):
        return default


def money(v, signed=False):
    return ('+' if signed and num(v) >= 0 else '') + f'${num(v):,.2f}'


def signed_money(v):
    v=num(v)
    return ('+' if v >= 0 else '−') + f'${abs(v):,.2f}'


def pct(v, signed=False):
    v=num(v)
    return ('+' if signed and v >= 0 else '') + f'{v:.2f}%'


def cls(v):
    return 'up' if num(v)>1e-7 else ('down' if num(v)<-1e-7 else 'neutral')


def read_csv(path: Path, limit=None):
    if not path.is_file():
        return []
    try:
        with path.open(newline='', encoding='utf-8-sig') as f:
            reader=csv.DictReader(f)
            if not reader.fieldnames:
                return []
            rows=[]
            for row in reader:
                rows.append(row)
                if limit and len(rows) > limit:
                    rows.pop(0)
            return rows
    except (OSError, csv.Error, UnicodeError):
        return []


def plot(history, names, starting):
    # Hourly points from saved simulation snapshots, up to the latest 45 days.
    series={name:history.get(name, [])[-1080:] for name in names}
    xs=sorted({t for vals in series.values() for t, _ in vals})
    if not xs:
        return '<div class="empty">No historical snapshots yet. A chart will appear as the next scans finish.</div>'
    vals=[v for pairs in series.values() for _,v in pairs]+[starting]
    low=min(vals);high=max(vals)
    if abs(high-low) < .02:
        low-=1;high+=1
    else:
        margin=max((high-low)*.12, .05)
        low-=margin;high+=margin
    W,H=760,278;L,R,T,B=62,18,20,38
    plot_w=W-L-R;plot_h=H-T-B
    def xx(ts):
        return L+((ts-xs[0])/(xs[-1]-xs[0]) if xs[-1]!=xs[0] else .5)*plot_w
    def yy(v):
        return T+(high-v)/(high-low)*plot_h
    parts=[f'<svg class="equity-svg" role="img" aria-label="Simulated equity history for {len(names)} bots" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    for i in range(5):
        val=low+(high-low)*i/4
        y=yy(val)
        parts.append(f'<path d="M {L} {y:.1f} H {W-R}" stroke="#263650" stroke-width="1"/>')
        parts.append(f'<text x="{L-9}" y="{y+4:.1f}" text-anchor="end" fill="#9aaac4" font-size="12">${val:,.0f}</text>')
    base=yy(starting)
    parts.append(f'<path d="M {L} {base:.1f} H {W-R}" stroke="#91a4c0" stroke-width="1.2" stroke-dasharray="4 5"/>')
    for i,name in enumerate(names):
        pts=series[name]
        if not pts:continue
        color=COLORS[i%len(COLORS)]
        if len(pts)>1:
            path=' '.join((('M' if j==0 else 'L')+f' {xx(t):.2f} {yy(v):.2f}') for j,(t,v) in enumerate(pts))
            parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>')
        x,y=xx(pts[-1][0]),yy(pts[-1][1])
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.4" fill="{color}"/>')
    if xs[-1] != xs[0]:
        tslist=[xs[0], xs[len(xs)//2], xs[-1]]
        for ts in tslist:
            d=datetime.fromtimestamp(ts,timezone.utc).strftime('%b %d %H:%M')
            parts.append(f'<text x="{xx(ts):.1f}" y="{H-12}" text-anchor="middle" fill="#9aaac4" font-size="12">{h(d)}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def parse_utc(st):
    try:
        return datetime.fromisoformat(str(st).replace('Z','+00:00')).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def render(state, cfg, data_dir, destination, names, mode='unknown', last_scan=0, errors=0):
    data_dir=Path(data_dir);destination=Path(destination)
    start=num(cfg.get('starting_balance'),1000)
    bots=state.get('bots',{})
    trades=read_csv(data_dir/'trades.csv',limit=10000)
    history=defaultdict(list)
    snapshot_count=0
    # Stream CSV without loading all snapshots into RAM. Retain last mark each hour.
    snapshot_path=data_dir/'leaderboard.csv'
    if snapshot_path.is_file():
        try:
            with snapshot_path.open(newline='',encoding='utf-8-sig') as inp:
                for row in csv.DictReader(inp):
                    snapshot_count+=1
                    name=row.get('bot')
                    if name not in names:continue
                    ts=parse_utc(row.get('utc'))
                    if ts is None:continue
                    val=num(row.get('equity'),start)
                    bucket=int(ts//3600)
                    if history[name] and history[name][-1][0]==bucket:
                        history[name][-1]=(bucket,ts,val)
                    else:
                        history[name].append((bucket,ts,val))
                        if len(history[name])>1080:
                            history[name].pop(0)
        except (OSError,csv.Error,UnicodeError):
            pass
    history={name:[(ts,val) for _,ts,val in vals] for name,vals in history.items()}
    by_trades=defaultdict(list)
    for trade in trades:
        if trade.get('bot') in names:by_trades[trade['bot']].append(trade)
    ranks=sorted(names,key=lambda n:num(bots.get(n,{}).get('last_equity'),start),reverse=True)
    original={name:i for i,name in enumerate(names)}
    rows=[];cards=[];positions=[]
    for rank,name in enumerate(ranks,1):
        bot=bots.get(name,{})
        eq=num(bot.get('last_equity'),start);pnl=eq-start;ret=100*pnl/start if start else 0
        completed=int(bot.get('wins',0))+int(bot.get('losses',0))
        winrate=(100*int(bot.get('wins',0))/completed) if completed else None
        fees=sum(num(t.get('fee')) for t in by_trades[name])
        status=('Paused' if bot.get('paused') else ('AI inactive' if name=='Hybrid AI' and not os.environ.get('OPENAI_API_KEY') else 'Paper active'))
        status_class='paused' if status!='Paper active' else 'active'
        ix=original[name];color=COLORS[ix%len(COLORS)]
        rows.append(f'<tr><td><span class="rank">{rank}</span><span class="dot" style="background:{color}"></span><span class="bot-name">{h(name)}</span></td>'
                    f'<td class="right">{money(eq)}</td><td class="right {cls(pnl)}">{pct(ret,True)}</td>'
                    f'<td class="center">{len(bot.get("positions",{}))}</td><td class="center">{completed}</td>'
                    f'<td class="right">{f"{winrate:.0f}%" if winrate is not None else "—"}</td>'
                    f'<td class="right">{num(bot.get("max_drawdown"))*100:.2f}%</td></tr>')
        cards.append(f'<article class="bot-card" style="--accent:{color}"><div class="card-title"><span class="dot" style="background:{color}"></span><b>{h(name)}</b><span class="status {status_class}">{h(status)}</span></div>'
                     f'<div class="card-total">{money(eq)}</div><div class="{cls(pnl)} card-return">{signed_money(pnl)} ({pct(ret,True)})</div>'
                     f'<div class="stat-grid"><div><small>Open positions</small><b>{len(bot.get("positions",{}))}</b></div><div><small>Closed trades</small><b>{completed}</b></div>'
                     f'<div><small>Realized P/L</small><b class="{cls(bot.get("realized_pnl"))}">{signed_money(bot.get("realized_pnl"))}</b></div>'
                     f'<div><small>Recorded fees</small><b>{money(fees)}</b></div></div></article>')
        if bot.get('positions'):
            for pair,pos in bot['positions'].items():
                # Current marks are approximations from latest scan, not live trade quotes.
                cost=num(pos.get('cost'))
                last_bid=num(pos.get('last_bid'), num(pos.get('entry')))
                est=num(pos.get('qty'))*last_bid*(1-num(cfg.get('fee_rate')))*(1-num(cfg.get('slippage_rate')))
                unreal=est-cost
                positions.append(f'<tr><td>{h(name)}</td><td>{h(pair)}</td><td class="right">{money(cost)}</td>'
                                 f'<td class="right {cls(unreal)}">{signed_money(unreal)}</td></tr>')
    recent=[]
    for trade in trades[-35:][::-1]:
        action=trade.get('action','')
        gain=trade.get('net_pnl','')
        when=str(trade.get('utc',''))[:16].replace('T',' ')
        recent.append(f'<tr><td>{h(when)}</td><td>{h(SHORT.get(trade.get("bot"),trade.get("bot","")))}</td>'
                      f'<td><span class="trade-tag {"buy" if action=="BUY" else "sell"}">{h(action)}</span></td>'
                      f'<td>{h(trade.get("pair",""))}</td><td class="right">{money(trade.get("fee"))}</td>'
                      f'<td class="right {cls(gain) if gain else "neutral"}">{signed_money(gain) if gain else "—"}</td></tr>')
    overview=sum(num(bots.get(n,{}).get('last_equity'),start) for n in names)
    total_start=start*len(names)
    active=sum(1 for n in names if bots.get(n,{}).get('positions'))
    now=datetime.now(timezone.utc)
    stamp=now.strftime('%b %d, %Y · %H:%M UTC')
    chart=plot(history,names,start)
    legend=''.join(f'<span><i style="background:{COLORS[i%len(COLORS)]}"></i>{h(SHORT.get(n,n))}</span>' for i,n in enumerate(names))
    page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><meta name="color-scheme" content="dark"><title>Coinbase Paper Competition · Dashboard</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:#e9eefb;background:#0a101c;font-variant-numeric:tabular-nums}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(150deg,#101c2f 0%,#0a101c 42%,#080e18 100%);min-height:100vh}main{max-width:1160px;margin:auto;padding:23px 18px 48px}
h1{font-size:clamp(23px,4vw,31px);letter-spacing:-.7px;margin:9px 0 4px}h2{font-size:18px;margin:0}p{line-height:1.5}.muted,small{color:#9daec7}.muted{font-size:13px}.eyebrow{text-transform:uppercase;letter-spacing:1.6px;font-size:11px;color:#8ea6c5;font-weight:800}.top{display:flex;gap:18px;flex-wrap:wrap;align-items:center;justify-content:space-between}.pill{background:#14362d;color:#7deac0;border:1px solid #27684f;padding:7px 11px;border-radius:99px;font-size:12px;font-weight:800}.banner{margin:20px 0;padding:12px 16px;border:1px solid #405575;background:#15243b;border-radius:12px;font-size:13px;color:#d5e3f7}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.panel,.metric,.bot-card{background:#121d30;border:1px solid #26354d;border-radius:16px;box-shadow:0 8px 22px #0002}.metric{padding:16px}.metric b{display:block;font-size:clamp(20px,2.5vw,28px);margin-top:6px}.metric small{font-size:12px}.up{color:#6be7ac!important}.down{color:#ff8f9c!important}.neutral{color:#dce5f3}.grid-section{margin-top:22px}.section-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:11px}.panel{padding:20px}.chart-wrap{overflow:hidden}.equity-svg{display:block;width:100%;height:auto;max-height:355px}.legend{display:flex;flex-wrap:wrap;gap:13px 19px;margin:12px 0 0}.legend span{font-size:12px;color:#b9c7da;display:flex;gap:7px;align-items:center}.legend i,.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex-shrink:0}.bots{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.bot-card{padding:17px;border-top:3px solid var(--accent)}.card-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:14px}.status{font-size:10px;font-weight:800;padding:4px 7px;border-radius:20px;margin-left:auto}.status.active{color:#83e7bd;background:#183b33}.status.paused{color:#f5c67e;background:#3d3025}.card-total{font-size:25px;font-weight:800;letter-spacing:-.5px;margin-top:14px}.card-return{font-size:13px;font-weight:700;margin-top:3px}.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:17px;padding-top:12px;border-top:1px solid #2d3b52}.stat-grid small{display:block;font-size:11px}.stat-grid b{display:block;margin-top:4px;font-size:13px}.table-wrap{overflow:auto;border-radius:11px}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}th{text-transform:uppercase;letter-spacing:.7px;font-size:10px;color:#a3b4cf;text-align:left;background:#17253b}td,th{padding:13px 10px;border-bottom:1px solid #25344a}tr:last-child td{border-bottom:0}.right{text-align:right}.center{text-align:center}.rank{display:inline-block;width:20px;color:#8299b7}.bot-name{font-weight:700;margin-left:8px}.trade-tag{font-size:11px;border-radius:6px;padding:4px 7px;font-weight:800}.trade-tag.buy{background:#174339;color:#8ce8bd}.trade-tag.sell{background:#3a324e;color:#d1b6ff}.empty{padding:45px 8px;color:#9daec7;text-align:center}.foot{font-size:12px;color:#879ab5;margin-top:25px}.subline{font-size:12px;color:#a8b8ce;margin-top:5px}
@media(max-width:830px){.kpis{grid-template-columns:repeat(2,1fr)}.bots{grid-template-columns:repeat(2,minmax(0,1fr))}.panel{padding:13px}}
@media(max-width:570px){main{padding:18px 12px 40px}.bots{grid-template-columns:1fr}.metric{padding:13px}.metric b{font-size:22px}.section-head{display:block}.section-head .muted{margin-top:5px}.bot-card{padding:15px}.top{gap:10px}}
</style></head><body><main>'''
    page+=f'<header class="top"><div><div class="eyebrow">Private · Live simulation monitor</div><h1>Coinbase Bot Competition</h1><div class="muted">Updated {h(stamp)} · auto-refresh every 60s</div></div><span class="pill">● PAPER TRADING</span></header>'
    page+='<div class="banner"><b>Simulated money only.</b> No real Coinbase orders. Reported prices, fees and slippage are approximations. All on Black can risk its full simulated balance. New listings are estimated from available history; they are not guaranteed token launches.</div>'
    page+=f'<section class="kpis"><div class="metric"><small>Total virtual equity</small><b>{money(overview)}</b></div><div class="metric"><small>Combined paper P/L</small><b class="{cls(overview-total_start)}">{signed_money(overview-total_start)}</b></div><div class="metric"><small>Scan cycles</small><b>{int(state.get("cycle",0)):,}</b></div><div class="metric"><small>Positions held</small><b>{sum(len(bots.get(n,{}).get("positions",{})) for n in names)}</b></div></section>'
    page+=f'<section class="grid-section"><div class="section-head"><h2>Equity history</h2><span class="muted">Saved snapshots · UTC · {snapshot_count:,} recorded snapshots</span></div><div class="panel chart-wrap">{chart}<div class="legend">{legend}</div></div></section>'
    page+=f'<section class="grid-section"><div class="section-head"><h2>Six-bot leaderboard</h2><span class="muted">Ranked by current virtual equity</span></div><div class="panel table-wrap"><table><thead><tr><th>Bot</th><th class="right">Equity</th><th class="right">Return</th><th class="center">Open</th><th class="center">Closed</th><th class="right">Win rate</th><th class="right">Max DD</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    page+=f'<section class="grid-section"><div class="section-head"><h2>Individual bot accounts</h2><span class="muted">Each account starts with {money(start)}; no shared funds</span></div><div class="bots">{"".join(cards)}</div></section>'
    page+=f'<section class="grid-section"><div class="section-head"><h2>Open positions</h2><span class="muted">Estimated P/L at last observed bid</span></div><div class="panel table-wrap"><table><thead><tr><th>Bot</th><th>Pair</th><th class="right">Cost</th><th class="right">Unrealized P/L</th></tr></thead><tbody>{"".join(positions) if positions else "<tr><td colspan=4 class=empty>No positions open</td></tr>"}</tbody></table></div></section>'
    page+=f'<section class="grid-section"><div class="section-head"><h2>Recent simulated trades</h2><span class="muted">Last 35 logged buys and sells</span></div><div class="panel table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Bot</th><th>Side</th><th>Pair</th><th class="right">Fee</th><th class="right">Net closed P/L</th></tr></thead><tbody>{"".join(recent) if recent else "<tr><td colspan=6 class=empty>No trades recorded yet</td></tr>"}</tbody></table></div></section>'
    page+=f'<p class="foot">Last scan: {last_scan} pairs · Reported errors: {errors} · {h(mode)} · This is a local static dashboard generated by the paper bot every scan. Historical charts use recorded snapshots only, not backfilled prices. More frequent scans do not guarantee better performance.</p></main></body></html>'
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_suffix('.tmp')
    temp.write_text(page,encoding='utf-8')
    os.replace(temp,destination)
