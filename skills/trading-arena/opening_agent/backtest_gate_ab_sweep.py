import os,sys,json
from datetime import datetime,date,time as dtime
from collections import defaultdict
from zoneinfo import ZoneInfo
sys.path.insert(0,'/home/tonygale/openclaw/skills/trading-arena')
def _env():
    for l in open('/home/tonygale/openclaw/.env'):
        l=l.strip()
        if l and not l.startswith('#') and '=' in l:
            k,_,v=l.partition('='); os.environ.setdefault(k,v)
_env()
from opening_agent import classifier as C
ET=ZoneInfo('America/New_York'); OPEN_T=dtime(9,30)
OFFSET=C.DEFAULTS['trade_offset']; CUT=30; ARM=7; MAXRISK=3.0; MINRANGE=0.05
SLOT=200.0; MAXN=5; NDAYS=60
CACHE='/home/tonygale/openclaw/skills/trading-arena/logs/backtest_cache_ibkr_tech'
syms=sorted(f[:-5] for f in os.listdir(CACHE) if f.endswith('.json') and not f.startswith('_'))

def load(s):
    raw=json.load(open(f'{CACHE}/{s}.json'))['bars']
    bars=[]
    for b in raw:
        et=datetime.fromisoformat(b['et'])
        bars.append({'et':et,'ts':et.timestamp(),'open':b['open'],'high':b['high'],'low':b['low'],'close':b['close']})
    bars.sort(key=lambda x:x['et'])
    closes=[b['close'] for b in bars]
    pre=[0.0]*(len(closes)+1)
    for i,c in enumerate(closes): pre[i+1]=pre[i]+c
    byday=defaultdict(list)
    for i,b in enumerate(bars): byday[b['et'].date()].append(i)
    return bars,pre,byday
def sma(pre,i,n): return (pre[i+1]-pre[i+1-n])/n if i+1>=n else None
def sim(bar,sess):
    e=bar['high']+OFFSET; stp=bar['low']-OFFSET; cut=bar['ts']+CUT*60
    win=[b for b in sess if b['ts']<=cut+1]; ent=ex=None
    for b in win:
        if b['ts']<=bar['ts']: continue
        if ent is None:
            if b['high']>=e: ent=e
            continue
        if b['low']<=stp: ex=stp; break
    if ent is None: return None
    if ex is None: ex=win[-1]['close']
    return (ex-ent)/ent*100

DATA={s:load(s) for s in syms}
alldays=sorted(set(d for s in syms for d in DATA[s][2]))
days=alldays[-NDAYS:]
print(f"universe={len(syms)}  sweep days={len(days)}  {days[0]} -> {days[-1]}",file=sys.stderr)

totA=totB=0.0; perday=[]; greenA=greenB=tradedA=tradedB=0
for d in days:
    # gather per-symbol opening index oi for this day
    candA=[]; candB=[]
    for s in syms:
        bars,pre,byday=DATA[s]
        idxs=byday.get(d)
        if not idxs: continue
        oi=next((i for i in idxs if bars[i]['et'].time()>=OPEN_T),None)
        if oi is None or oi<200: continue
        # Scenario A: rolling arm gate ON
        for k in range(oi,oi+ARM):
            if k>=len(bars): break
            v=C.classify_opening(s,bars[k],bars[max(0,k-60):k],sma(pre,k,20),sma(pre,k,200))
            if v.decision=='MATCH_LONG':
                e=bars[k]['high']+OFFSET; stp=bars[k]['low']-OFFSET; risk=(e-stp)/e*100
                if risk<=MAXRISK and (e-stp)>=MINRANGE:
                    candA.append((k,s,sim(bars[k],bars[k:k+25])))
                break
        # Scenario B: gap-up + 9:30 breakout
        pc=bars[oi-1]['close']; gap=(bars[oi]['open']-pc)/pc*100
        candB.append((gap,s,sim(bars[oi],bars[oi:oi+25])))
    candA.sort(key=lambda x:x[0]); pickA=candA[:MAXN]
    candB.sort(key=lambda x:-x[0]); pickB=candB[:MAXN]
    dA=sum(SLOT*(p[2] or 0)/100 for p in pickA)
    dB=sum(SLOT*(p[2] or 0)/100 for p in pickB)
    totA+=dA; totB+=dB; perday.append((str(d),round(dA,2),round(dB,2),len(pickA)))
    if pickA: tradedA+=1
    if pickB: tradedB+=1
    if dA>0: greenA+=1
    if dB>0: greenB+=1

print(f"\n===== 60-DAY SWEEP ({days[0]} -> {days[-1]}), $1000, $200 equal slots, full-slot, gross =====")
print(f"{'metric':<34}{'A: AS-BUILT':>16}{'B: gate-off+gap':>18}")
print(f"{'total P&L on $1000':<34}{'$%+.2f'%totA:>16}{'$%+.2f'%totB:>18}")
print(f"{'avg per trading day':<34}{'$%+.2f'%(totA/len(days)):>16}{'$%+.2f'%(totB/len(days)):>18}")
print(f"{'days with >=1 trade':<34}{tradedA:>16}{tradedB:>18}")
print(f"{'green days (P&L>0)':<34}{greenA:>16}{greenB:>18}")
print(f"{'return on $1000 over 60d':<34}{'%+.1f%%'%(totA/1000*100):>16}{'%+.1f%%'%(totB/1000*100):>18}")
# best/worst days
sA=sorted(perday,key=lambda x:x[1]); sB=sorted(perday,key=lambda x:x[2])
print(f"\nA worst day {sA[0][0]} ${sA[0][1]}; best {sA[-1][0]} ${sA[-1][1]}")
print(f"B worst day {sB[0][0]} ${sB[0][2]}; best {sB[-1][0]} ${sB[-1][2]}")
json.dump({'range':[str(days[0]),str(days[-1])],'days':len(days),'A_total':round(totA,2),'B_total':round(totB,2),
           'A_avg':round(totA/len(days),2),'B_avg':round(totB/len(days),2),'greenA':greenA,'greenB':greenB,
           'tradedA':tradedA,'tradedB':tradedB,'perday':perday},open('/tmp/sweep60_result.json','w'))
print("\nsaved /tmp/sweep60_result.json")

# --- commission + trade-count addendum (re-derive fills) ---
nA=nB=0
for d in days:
    candA=[]; candB=[]
    for s in syms:
        bars,pre,byday=DATA[s]
        idxs=byday.get(d)
        if not idxs: continue
        oi=next((i for i in idxs if bars[i]['et'].time()>=OPEN_T),None)
        if oi is None or oi<200: continue
        for k in range(oi,oi+ARM):
            if k>=len(bars): break
            v=C.classify_opening(s,bars[k],bars[max(0,k-60):k],sma(pre,k,20),sma(pre,k,200))
            if v.decision=='MATCH_LONG':
                e=bars[k]['high']+OFFSET; stp=bars[k]['low']-OFFSET; risk=(e-stp)/e*100
                if risk<=MAXRISK and (e-stp)>=MINRANGE:
                    candA.append((k,s,sim(bars[k],bars[k:k+25])))
                break
        pc=bars[oi-1]['close']; gap=(bars[oi]['open']-pc)/pc*100
        candB.append((gap,s,sim(bars[oi],bars[oi:oi+25])))
    candA.sort(key=lambda x:x[0]); candB.sort(key=lambda x:-x[0])
    nA+=sum(1 for p in candA[:MAXN] if p[2] is not None)
    nB+=sum(1 for p in candB[:MAXN] if p[2] is not None)
print(f"\nfilled trades over 60d:  A={nA}   B={nB}")
for rt in (1.0,2.0):
    print(f"  net of ${rt:.0f}/round-trip commission:   A=${totA-nA*rt:+.2f}   B=${totB-nB*rt:+.2f}")
