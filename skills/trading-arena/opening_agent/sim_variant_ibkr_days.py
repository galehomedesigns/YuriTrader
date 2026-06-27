#!/usr/bin/env python3
"""Add the last N trading days from the IBKR broad cache (231 names, the full
selection) to the variant dashboard, as per-day tabs with the SAME shape the
dashboard renders (candles + SMA20/200 + gap + location + variant entries).

Per day: gap 1-6% candidates → variant arm (power + close>SMA200, no TIGHT) →
capped-1.5% stop, 2R target, breakeven, 20-min sell-off. Keeps the top-N by gap.
Merges into logs/opening_sim_variant.json alongside the 6/26 TV day. IBKR feed
(flagged), distinct from the 6/26 TradingView capture.
"""
import os, sys, glob, json, math
from datetime import datetime, date, time as dtime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
def _env():
    for line in open("/home/tonygale/openclaw/.env"):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            if k and v: os.environ.setdefault(k, v)
_env()
from opening_agent import classifier as C

ET = ZoneInfo("America/New_York")
OPEN_T, ARM_END, WIN_END = dtime(9, 30), dtime(9, 44), dtime(10, 20)
SELLOFF_MIN, RR = 30, 3.0      # SWEET SPOT: 3R target, 30-min hold
GAP_MIN, GAP_MAX = 0.5, 4.0    # SWEET SPOT: modest gaps, cap the big gappers
SLOT = 200.0; OFFSET = C.DEFAULTS["trade_offset"]
N_DAYS = 10; TOP_PER_DAY = 8
MAX_PRICE = 300.0              # remove high-priced stocks (>$300) from the dashboard

def panel(rows):
    """Build a {totals, rows} panel: totals over ALL rows, charts for the top-N by P&L."""
    pcts=[r["realized_pl"]/r["position_cost"]*100 for r in rows if r.get("position_cost")]
    disp=sorted(rows,key=lambda r:-(r.get("realized_pl") or 0))[:TOP_PER_DAY]
    return {"totals":{"realized_pl":round(sum(r["realized_pl"] for r in rows),2),
                      "avg_pct":round(sum(pcts)/len(pcts),3) if pcts else 0,
                      "names":len(rows),"shown":len(disp),
                      "held_to_1020_pl":round(sum((r["held_to_1020_pl"] or 0) for r in rows),2)},
            "rows":disp}
CACHE = os.path.join(HERE, "..", "logs", "backtest_cache_ibkr_broad")
OUT = os.path.join(HERE, "..", "logs", "opening_sim_variant.json")

def roll(xs, n):
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

def sim_one(bars,s20,s200,idxs,day,sym,mode):
    """Full sim producing row+timeline for one candidate, in 'sweet' or 'baseline' mode.
    sweet:    arm power+close>SMA200 (no TIGHT), wick stop, 3R target, breakeven@1R, 30-min.
    baseline: arm classifier MATCH_LONG (TIGHT on, loc by open), wick stop, breakeven@1R +
              push-trail (prior-bar low), no fixed target, 30-min sell-off."""
    arm=None
    for i in idxs:
        if bars[i]["dt"].time()>ARM_END: break
        if s200[i] is None: continue
        prior=bars[max(0,i-30):i]
        ok=(C.bar_signal(bars[i],prior)>0 and bars[i]["close"]>s200[i]) if mode=="sweet" \
           else (C.classify_opening("S",bars[i],prior,s20[i],s200[i]).decision=="MATCH_LONG")
        if ok: arm=i; break
    if arm is None: return None
    entry=round(bars[arm]["high"]+OFFSET,2); stop=round(bars[arm]["low"]-OFFSET,2)
    if stop>=entry: return None
    risk=round(entry-stop,4); shares=max(1,math.floor(SLOT/entry))
    target=round(entry+RR*risk,4) if mode=="sweet" else None
    loc=("above-both" if bars[arm]["close"]>max(s20[arm],s200[arm]) else "above-200/below-20")
    selloff=datetime.combine(day,OPEN_T,ET)+timedelta(minutes=SELLOFF_MIN)
    tl=[]; in_pos=False; cur=stop; be=False; exitrec=None; ent_dt=None; sesshi=None; prev_low=None
    for i in idxs:
        b=bars[i]; t=b["dt"]
        if t.time()>WIN_END: break
        ev=[]
        if t.time()==bars[arm]["dt"].time():
            ev.append(f"ARMED ${entry:.2f}, stop ${stop:.2f}"+(f", target ${target:.2f} (3R)" if target else " (trail)"))
        if not in_pos and exitrec is None and i>arm and b["high"]>=entry:
            in_pos=True; ent_dt=t; prev_low=b["low"]; ev.append(f"ENTRY {shares} @ ${entry:.2f}")
        if in_pos:
            sesshi=b["high"] if sesshi is None else max(sesshi,b["high"])
            if target and b["high"]>=target: exitrec={"time":t.strftime("%H:%M"),"price":target,"reason":"3R target hit","qty":shares};ev.append(f"TARGET exit ${target:.2f}");in_pos=False
            elif b["low"]<=cur: exitrec={"time":t.strftime("%H:%M"),"price":cur,"reason":("breakeven" if be else "protective")+" stop hit","qty":shares};ev.append(f"STOP exit ${cur:.2f}");in_pos=False
            else:
                if not be and b["high"]>=entry+risk: cur=entry;be=True;ev.append(f"+1R → breakeven ${entry:.2f}")
                if mode=="baseline" and be and prev_low is not None and prev_low-OFFSET>cur:
                    cur=round(prev_low-OFFSET,4);ev.append(f"trail stop → ${cur:.2f}")
                if t>=selloff: exitrec={"time":t.strftime("%H:%M"),"price":round(b["close"],4),"reason":f"{SELLOFF_MIN}-min sell-off","qty":shares};ev.append(f"{SELLOFF_MIN}-MIN SELL-OFF ${b['close']:.2f}");in_pos=False
            prev_low=b["low"]
        tl.append({"t":t.strftime("%H:%M"),"o":b["open"],"h":b["high"],"l":b["low"],"c":b["close"],
                   "sma20":round(s20[i],3) if s20[i] else None,"sma200":round(s200[i],3) if s200[i] else None,
                   "shares":(shares if in_pos or (exitrec and exitrec["time"]==t.strftime("%H:%M")) else 0),
                   "stop":round(cur,4) if (in_pos or t.time()>=bars[arm]["dt"].time()) else None,
                   "target":target,"event":"; ".join(ev),
                   "mtm":round((b["close"]-entry)*shares,2) if in_pos else None,
                   "if_held":round((b["close"]-entry)*shares,2) if ent_dt else None})
    if not exitrec: return None
    realized=round((exitrec["price"]-entry)*shares,2)
    held=round((tl[-1]["c"]-entry)*shares,2) if (tl and ent_dt) else None
    pos=round(shares*entry,2)
    return {"symbol":sym,"tv":sym,"armed":True,"mode":mode,"arm_t":bars[arm]["dt"].strftime("%H:%M"),
            "sma20":round(s20[arm],2),"sma200":round(s200[arm],2),"loc":loc,
            "entry":entry,"stop":stop,"target":target,"risk_per_share":risk,
            "risk_pct":round(risk/entry*100,2),"shares":shares,"position_cost":pos,
            "exit":exitrec,"realized_pl":realized,
            "held_to_1020_pl":held,"session_high":round(sesshi,4) if sesshi else None,"timeline":tl}

def main():
    syms={}
    for p in glob.glob(os.path.join(CACHE,"*.json")):
        try: syms[os.path.basename(p)[:-5]]=load(p)
        except Exception: pass
    all_days=sorted({d for (_,_,_,bd) in syms.values() for d in bd})
    last=all_days[-N_DAYS:]
    days_out=[]
    for di_day in last:
        sweet=[]; base=[]
        for s,(bars,s20,s200,byday) in syms.items():
            if di_day not in byday: continue
            dates=sorted(byday); pos=dates.index(di_day)
            if pos==0: continue
            idxs=byday[di_day]
            if len(idxs)<12: continue
            pclose=bars[byday[dates[pos-1]][-1]]["close"]; o=bars[idxs[0]]["open"]
            gap=(o-pclose)/pclose*100
            if not (GAP_MIN<=gap<=GAP_MAX): continue
            if o>MAX_PRICE: continue                       # remove high-priced stocks (>$300)
            for mode,bucket in (("sweet",sweet),("baseline",base)):
                r=sim_one(bars,s20,s200,idxs,di_day,s,mode)
                if r:
                    r["premarket_gap_pct"]=round(gap,2); r["prev_close"]=round(pclose,2); r["today_open"]=round(o,2)
                    bucket.append(r)
        days_out.append({"day":di_day.isoformat(),"source":"IBKR 2-min (broad 231-name cache)",
                         "sweet":panel(sweet),"baseline":panel(base)})
    # merge with existing (TV 6/26) day
    existing=json.load(open(OUT))
    tv_days=[d for d in existing["days"] if d["day"] not in {x["day"] for x in days_out}]
    existing["days"]=sorted(tv_days+days_out, key=lambda d:d["day"], reverse=True)
    json.dump(existing,open(OUT,"w"),indent=2,default=str)
    print(json.dumps({"added_days":[{"day":d["day"],"sweet$":d["sweet"]["totals"]["realized_pl"],
                                     "base$":d["baseline"]["totals"]["realized_pl"]} for d in days_out]},indent=2))

if __name__=="__main__":
    main()
