#!/usr/bin/env python3
"""Parameter sweep over the last 10 trading days (IBKR broad 231-name cache) to
find the profit sweet spot. Size-agnostic (per-trade % returns => 'can buy any
stock'). Grids: gap band x stop type/size x exit strategy x sell-off timing x
location. Reports the top combos + the dominant pattern.

Exit modes:
  selloff   - fixed initial stop, exit at sell-off (or stop)
  be        - + move stop to breakeven at +1R
  t2R / t3R - breakeven + take-profit at 2R / 3R
  trailLoose- breakeven, then trail stop to the PRIOR bar's low (wick) each bar
  trailTight- trail stop to the CURRENT bar's low each bar (aggressive/push-like)
Stop modes: 'wick' (one-bar low = the candle wick) or a hard cap 0.5/1/1.5/2/3 %.
"""
import os, sys, glob, json, math, itertools
from datetime import datetime, time as dtime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
def _env():
    for line in open("/home/tonygale/openclaw/.env"):
        s=line.strip()
        if s and not s.startswith("#") and "=" in s:
            k,_,v=s.partition("=")
            if k and v: os.environ.setdefault(k,v)
_env()
from opening_agent import classifier as C

ET=ZoneInfo("America/New_York")
OPEN_T,ARM_END,WIN_END=dtime(9,30),dtime(9,44),dtime(10,50)
OFFSET=C.DEFAULTS["trade_offset"]
CACHE=os.path.join(HERE,"..","logs","backtest_cache_ibkr_broad")
N_DAYS=10; MIN_TRADES=40

def roll(xs,n):
    out=[None]*len(xs); s=0.0
    for i,v in enumerate(xs):
        s+=v
        if i>=n: s-=xs[i-n]
        if i>=n-1: out[i]=s/n
    return out

def load(path):
    d=json.load(open(path)); bars=[]
    for b in d.get("bars",[]):
        dt=datetime.fromisoformat(b["et"])
        if OPEN_T<=dt.time()<=dtime(16,0):
            bars.append({"dt":dt,"open":b["open"],"high":b["high"],"low":b["low"],"close":b["close"]})
    bars.sort(key=lambda x:x["dt"])
    closes=[x["close"] for x in bars]
    s20,s200=roll(closes,20),roll(closes,200)
    byday=defaultdict(list)
    for i,x in enumerate(bars): byday[x["dt"].date()].append(i)
    return bars,s20,s200,byday

def build_records():
    syms={}
    for p in glob.glob(os.path.join(CACHE,"*.json")):
        try: syms[os.path.basename(p)[:-5]]=load(p)
        except Exception: pass
    all_days=sorted({d for (_,_,_,bd) in syms.values() for d in bd})
    last=all_days[-N_DAYS:]
    recs=[]
    for s,(bars,s20,s200,byday) in syms.items():
        dates=sorted(byday)
        for day in last:
            if day not in byday: continue
            pos=dates.index(day)
            if pos==0: continue
            idxs=byday[day]
            if len(idxs)<12: continue
            pclose=bars[byday[dates[pos-1]][-1]]["close"]; o=bars[idxs[0]]["open"]
            gap=(o-pclose)/pclose*100
            # window bars 9:30-10:50
            win=[i for i in idxs if bars[i]["dt"].time()<=WIN_END]
            wb=[{"dt":bars[i]["dt"],"open":bars[i]["open"],"high":bars[i]["high"],
                 "low":bars[i]["low"],"close":bars[i]["close"]} for i in win]
            # arm by close-above-200 and by open-above-both (both power-bar gated, no TIGHT)
            arm_c=arm_o=None
            for k,i in enumerate(win):
                if bars[i]["dt"].time()>ARM_END: break
                if s200[i] is None: continue
                if C.bar_signal(bars[i],bars[max(0,i-30):i])>0:
                    if arm_c is None and bars[i]["close"]>s200[i]: arm_c=k
                    if arm_o is None and bars[i]["open"]>max(s20[i],s200[i]): arm_o=k
            recs.append({"sym":s,"day":day,"gap":gap,"wb":wb,"arm_c":arm_c,"arm_o":arm_o})
    return recs,last

def trade(wb,ak,stopmode,exitmode,selloff_min):
    if ak is None: return None
    a=wb[ak]; entry=a["high"]+OFFSET; onebar=a["low"]-OFFSET
    if stopmode=="wick": stop=onebar
    else: stop=max(onebar, entry*(1-stopmode/100))   # hard cap at N% (never looser than wick)
    if stop>=entry: return None
    risk=entry-stop
    tgt = entry+2*risk if exitmode=="t2R" else entry+3*risk if exitmode=="t3R" else None
    use_be = exitmode in ("be","t2R","t3R","trailLoose")
    trail = "loose" if exitmode=="trailLoose" else "tight" if exitmode=="trailTight" else None
    day=a["dt"].date(); selloff=datetime.combine(day,OPEN_T,ET)+timedelta(minutes=selloff_min)
    in_pos=False; cur=stop; be=False; prev_low=None
    for b in wb[ak+1:]:
        if not in_pos and b["high"]>=entry: in_pos=True; prev_low=b["low"]
        if in_pos:
            if tgt and b["high"]>=tgt: return (tgt-entry)/entry*100
            if b["low"]<=cur: return (cur-entry)/entry*100
            if use_be and not be and b["high"]>=entry+risk: cur=entry; be=True
            if trail=="tight": cur=max(cur,b["low"]-OFFSET)
            elif trail=="loose" and be and prev_low is not None: cur=max(cur,prev_low-OFFSET)
            if b["dt"]>=selloff: return (b["close"]-entry)/entry*100
            prev_low=b["low"]
    return None

def main():
    recs,last=build_records()
    print(f"records: {len(recs)} symbol-days over {len(last)} days ({last[0]}..{last[-1]})", file=sys.stderr)
    GMIN=[0.5,1,2,3]; GMAX=[4,6,10,100]
    STOP=["wick",0.5,1,1.5,2,3]
    EXIT=["selloff","be","t2R","t3R","trailLoose","trailTight"]
    SELL=[20,30]; LOC=["close","open"]
    results=[]
    for gmin,gmax,sm,em,so,loc in itertools.product(GMIN,GMAX,STOP,EXIT,SELL,LOC):
        rets=[]
        for r in recs:
            if not (gmin<=r["gap"]<=gmax): continue
            ak=r["arm_c"] if loc=="close" else r["arm_o"]
            ret=trade(r["wb"],ak,sm,em,so)
            if ret is not None: rets.append(ret)
        if len(rets)<MIN_TRADES: continue
        wins=[x for x in rets if x>0]
        results.append({"gap":f"{gmin}-{gmax}","stop":sm,"exit":em,"selloff":so,"loc":loc,
                        "trades":len(rets),"win%":round(len(wins)/len(rets)*100,1),
                        "avg%":round(sum(rets)/len(rets),3),"total%":round(sum(rets),1),
                        "best":round(max(rets),1),"worst":round(min(rets),1)})
    results.sort(key=lambda x:-x["total%"])
    print("=== TOP 15 by total return % ===")
    print(f"{'gap':7}{'stop':>6}{'exit':>11}{'sell':>5}{'loc':>6}{'trades':>7}{'win%':>6}{'avg%':>7}{'total%':>8}")
    for r in results[:15]:
        print(f"{r['gap']:7}{str(r['stop']):>6}{r['exit']:>11}{r['selloff']:>5}{r['loc']:>6}{r['trades']:>7}{r['win%']:>6}{r['avg%']:>7}{r['total%']:>8}")
    print("\n=== best by AVG % / trade (>=80 trades) ===")
    bya=sorted([r for r in results if r['trades']>=80], key=lambda x:-x["avg%"])[:8]
    for r in bya:
        print(f"{r['gap']:7}{str(r['stop']):>6}{r['exit']:>11}{r['selloff']:>5}{r['loc']:>6}{r['trades']:>7}{r['win%']:>6}{r['avg%']:>7}{r['total%']:>8}")
    # pattern: average total% by each param value (across all combos)
    print("\n=== PATTERN (avg total% across combos, holding others varied) ===")
    for field in ["gap","stop","exit","selloff","loc"]:
        agg=defaultdict(list)
        for r in results: agg[r[field]].append(r["total%"])
        line=" | ".join(f"{k}: {round(sum(v)/len(v),1)}" for k,v in sorted(agg.items(), key=lambda kv:-sum(kv[1])/len(kv[1])))
        print(f"  {field:8}: {line}")
    json.dump(results, open(os.path.join(HERE,"..","logs","sweep_variant_results.json"),"w"), indent=1)

if __name__=="__main__":
    main()
