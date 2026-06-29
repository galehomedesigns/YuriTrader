import os,sys,json
from datetime import datetime,date,time as dtime
from zoneinfo import ZoneInfo
sys.path.insert(0,'/home/tonygale/openclaw/skills/trading-arena')
def _env():
    for l in open('/home/tonygale/openclaw/.env'):
        l=l.strip()
        if l and not l.startswith('#') and '=' in l:
            k,_,v=l.partition('='); os.environ.setdefault(k,v)
_env()
from opening_agent import classifier as C
import shared.indicators as ind
ET=ZoneInfo('America/New_York'); TGT=date(2026,3,26); OPEN_T=dtime(9,30)
OFFSET=C.DEFAULTS['trade_offset']; CUT=30; ARM=7; MAXRISK=3.0; MINRANGE=0.05
SLOT=200.0; MAXN=5; CACHE='/tmp/day60_bars'
def load(s):
    r=json.load(open(f'{CACHE}/{s}.json'))
    out=[]
    for b in r:
        et=datetime.fromisoformat(b['et'])
        if OPEN_T<=et.time()<dtime(16,0):
            out.append({'et':et,'date':et.timestamp(),'open':b['open'],'high':b['high'],'low':b['low'],'close':b['close']})
    out.sort(key=lambda x:x['et']); return out
def smas(rows,k):
    cl=[b['close'] for b in rows[:k+1]]; return ind.sma(cl,20),ind.sma(cl,200)
def cls(rows,k):
    f,s=smas(rows,k); return C.classify_opening('x',rows[k],rows[max(0,k-60):k],f,s)
def sim(bar,sess):
    e=bar['high']+OFFSET; stp=bar['low']-OFFSET; cut=bar['date']+CUT*60
    win=[b for b in sess if b['date']<=cut+1]; ent=ex=None
    for b in win:
        if b['date']<=bar['date']: continue
        if ent is None:
            if b['high']>=e: ent=e
            continue
        if b['low']<=stp: ex=stp; break
    if ent is None: return None
    if ex is None: ex=win[-1]['close']
    return {'e':e,'stp':stp,'exit':ex,'pct':(ex-ent)/ent*100,'stopped':abs(ex-stp)<1e-9}
syms=sorted(f[:-5] for f in os.listdir(CACHE) if f.endswith('.json'))
B={}; oi_map={}
for s in syms:
    rows=load(s); B[s]=rows
    oi=next((i for i,b in enumerate(rows) if b['et'].date()==TGT and b['et'].time()>=OPEN_T),None)
    oi_map[s]=oi if (oi is not None and oi>=200 and oi+ARM<len(rows)) else None
ok=[s for s in syms if oi_map[s] is not None]
print(f"cached={len(syms)}  usable(>=200 priors)={len(ok)}")
# A: gate ON rolling arm
arms=[]
for s in ok:
    rows=B[s]; oi=oi_map[s]
    for k in range(oi,oi+ARM):
        if cls(rows,k).decision=='MATCH_LONG':
            bar=rows[k]; e=bar['high']+OFFSET; stp=bar['low']-OFFSET; risk=(e-stp)/e*100
            if risk>MAXRISK or (e-stp)<MINRANGE: arms.append([s,rows[k]['et'].strftime('%H:%M'),k,'CAPPED',risk,None]); break
            arms.append([s,rows[k]['et'].strftime('%H:%M'),k,'OK',risk,sim(bar,rows[k:k+25])]); break
tr=[a for a in arms if a[3]=='OK']; tr.sort(key=lambda a:a[2]); picksA=tr[:MAXN]
print(f"\n[A] AS-BUILT (gate ON, rolling-arm, top-{MAXN} by arm time)")
print(f"    armed={len(arms)} tradable(after risk-cap)={len(tr)}")
totA=0; rowsA=[]
for s,tag,k,st,risk,sm in picksA:
    if sm is None: print(f"    {s:5} arm {tag} no-fill $0"); rowsA.append([s,tag,None,0]); continue
    d=SLOT*sm['pct']/100; totA+=d; rowsA.append([s,tag,round(sm['pct'],2),round(d,2)])
    print(f"    {s:5} arm {tag} {'STOP' if sm['stopped'] else 'cut '} {sm['pct']:+6.2f}%  ${d:+7.2f}")
print(f"    >>> A total / $1000: ${totA:+.2f}")
# B: gate OFF, gap-up top5, 9:30 breakout
gaps=[(s,(B[s][oi_map[s]]['open']-B[s][oi_map[s]-1]['close'])/B[s][oi_map[s]-1]['close']*100) for s in ok]
gaps.sort(key=lambda x:-x[1]); top5=[g[0] for g in gaps[:MAXN]]
print(f"\n[B] SMALL-CHANGE (gate OFF, top-{MAXN} gap-up, 9:30 breakout)")
print(f"    top gappers: {[(s,round(g,1)) for s,g in gaps[:MAXN]]}")
totB=0; rowsB=[]
for s in top5:
    rows=B[s]; oi=oi_map[s]; sm=sim(rows[oi],rows[oi:oi+25]); g=dict(gaps)[s]
    if sm is None: print(f"    {s:5} gap{g:+.1f}% no-trigger $0"); rowsB.append([s,round(g,2),None,0]); continue
    d=SLOT*sm['pct']/100; totB+=d; rowsB.append([s,round(g,2),round(sm['pct'],2),round(d,2)])
    print(f"    {s:5} gap{g:+5.1f}% {'STOP' if sm['stopped'] else 'cut '} {sm['pct']:+6.2f}%  ${d:+7.2f}")
print(f"    >>> B total / $1000: ${totB:+.2f}")
print(f"\nDIFF (B-A): ${totB-totA:+.2f}")
json.dump({'date':str(TGT),'cached':len(syms),'usable':len(ok),'A_total':round(totA,2),'B_total':round(totB,2),
           'A':rowsA,'B':rowsB,'armed':len(arms),'tradable':len(tr)},open('/tmp/day60_result.json','w'))
