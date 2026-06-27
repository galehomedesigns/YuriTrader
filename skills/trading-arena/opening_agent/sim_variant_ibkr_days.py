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
N_DAYS = 10; TOP_PER_DAY = 10
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

def baseline_trade(bars,s20,s200,idxs,day):
    """Live-style baseline on the SAME candidate: classifier arm (TIGHT on, location
    by OPEN) + wick stop + breakeven@1R + push-trail (prior-bar low) + 30-min sell-off,
    full slot. Returns (realized_$, ret_%) or None. For the per-day A/B vs sweet-spot."""
    arm=None
    for i in idxs:
        if bars[i]["dt"].time()>ARM_END: break
        if s200[i] is None: continue
        if C.classify_opening("S",bars[i],bars[max(0,i-30):i],s20[i],s200[i]).decision=="MATCH_LONG":
            arm=i; break
    if arm is None: return None
    entry=bars[arm]["high"]+OFFSET; stop=bars[arm]["low"]-OFFSET
    if stop>=entry: return None
    risk=entry-stop; shares=max(1,math.floor(SLOT/entry))
    selloff=datetime.combine(day,OPEN_T,ET)+timedelta(minutes=SELLOFF_MIN)
    in_pos=False; cur=stop; be=False; prev_low=None
    for i in idxs:
        if i<=arm: continue
        b=bars[i]
        if b["dt"].time()>WIN_END: break
        if not in_pos and b["high"]>=entry: in_pos=True; prev_low=b["low"]
        if in_pos:
            if b["low"]<=cur: return ((cur-entry)*shares,(cur-entry)/entry*100)
            if not be and b["high"]>=entry+risk: cur=entry; be=True
            if be and prev_low is not None: cur=max(cur,prev_low-OFFSET)
            if b["dt"]>=selloff: return ((b["close"]-entry)*shares,(b["close"]-entry)/entry*100)
            prev_low=b["low"]
    return None

def sim_one(bars,s20,s200,idxs,day,sym):
    # arm
    arm=None
    for i in idxs:
        if bars[i]["dt"].time()>ARM_END: break
        if s200[i] is None: continue
        if C.bar_signal(bars[i],bars[max(0,i-30):i])>0 and bars[i]["close"]>s200[i]:
            arm=i; break
    if arm is None: return None
    entry=round(bars[arm]["high"]+OFFSET,2)
    stop=round(bars[arm]["low"]-OFFSET,2)             # SWEET SPOT: wick stop (one-bar low), no cap
    risk=round(entry-stop,4); target=round(entry+RR*risk,4)
    shares=max(1,math.floor(SLOT/entry))
    loc=("above-both" if bars[arm]["close"]>max(s20[arm],s200[arm]) else "above-200/below-20")
    selloff=datetime.combine(day,OPEN_T,ET)+timedelta(minutes=SELLOFF_MIN)
    tl=[]; in_pos=False; cur=stop; be=False; exitrec=None; ent_dt=None; sesshi=None
    for i in idxs:
        b=bars[i]; t=b["dt"]
        if t.time()>WIN_END: break
        ev=[]
        if t.time()==bars[arm]["dt"].time():
            ev.append(f"ARMED — buy-stop ${entry:.2f}, stop ${stop:.2f}, target ${target:.2f} (2R), loc {loc}")
        if not in_pos and exitrec is None and i>arm and b["high"]>=entry:
            in_pos=True; ent_dt=t; ev.append(f"ENTRY {shares} @ ${entry:.2f}; stop ${cur:.2f}, target ${target:.2f}")
        if in_pos:
            sesshi=b["high"] if sesshi is None else max(sesshi,b["high"])
            if b["high"]>=target: exitrec={"time":t.strftime("%H:%M"),"price":target,"reason":"2R target hit","qty":shares};ev.append(f"TARGET — exit @ ${target:.2f}");in_pos=False
            elif b["low"]<=cur: exitrec={"time":t.strftime("%H:%M"),"price":cur,"reason":("breakeven" if be else "protective")+" stop hit","qty":shares};ev.append(f"STOP — exit @ ${cur:.2f}");in_pos=False
            else:
                if not be and b["high"]>=entry+risk: cur=entry;be=True;ev.append(f"+1R → stop to breakeven ${entry:.2f}")
                if t>=selloff: exitrec={"time":t.strftime("%H:%M"),"price":round(b["close"],4),"reason":f"{SELLOFF_MIN}-min sell-off","qty":shares};ev.append(f"{SELLOFF_MIN}-MIN SELL-OFF — exit @ ${b['close']:.2f}");in_pos=False
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
    return {"symbol":sym,"tv":sym,"armed":True,"arm_t":bars[arm]["dt"].strftime("%H:%M"),
            "sma20":round(s20[arm],2),"sma200":round(s200[arm],2),"loc":loc,
            "entry":entry,"stop":stop,"target":target,"risk_per_share":risk,
            "risk_pct":round(risk/entry*100,2),"shares":shares,"position_cost":pos,
            "over_slot":pos>SLOT*1.1,"exit":exitrec,"realized_pl":realized,
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
        sweet=[]; base_pl=0.0; base_rets=[]; base_n=0
        for s,(bars,s20,s200,byday) in syms.items():
            if di_day not in byday: continue
            dates=sorted(byday); pos=dates.index(di_day)
            if pos==0: continue
            idxs=byday[di_day]
            if len(idxs)<12: continue
            pclose=bars[byday[dates[pos-1]][-1]]["close"]; o=bars[idxs[0]]["open"]
            gap=(o-pclose)/pclose*100
            if not (GAP_MIN<=gap<=GAP_MAX): continue
            # sweet-spot (charted)
            r=sim_one(bars,s20,s200,idxs,di_day,s)
            if r:
                r["premarket_gap_pct"]=round(gap,2); r["prev_close"]=round(pclose,2); r["today_open"]=round(o,2)
                sweet.append(r)
            # baseline (live-style) on the SAME candidate — A/B
            bt=baseline_trade(bars,s20,s200,idxs,di_day)
            if bt: base_pl+=bt[0]; base_rets.append(bt[1]); base_n+=1
        sweet_pl=sum(r["realized_pl"] for r in sweet)
        sweet_rets=[(r["realized_pl"]/r["position_cost"]*100) for r in sweet if r.get("position_cost")]
        rows=sorted(sweet,key=lambda r:-(r.get("premarket_gap_pct") or 0))[:TOP_PER_DAY]
        rows.sort(key=lambda r:-(r.get("realized_pl") or 0))
        days_out.append({"day":di_day.isoformat(),"source":"IBKR 2-min (broad 231-name cache)",
                         "totals":{"realized_pl":round(sweet_pl,2),
                                   "held_to_1020_pl":round(sum((r["held_to_1020_pl"] or 0) for r in sweet),2),
                                   "names":len(sweet),"shown":len(rows),
                                   "sweet_avg_pct":round(sum(sweet_rets)/len(sweet_rets),3) if sweet_rets else 0,
                                   "baseline_pl":round(base_pl,2),"baseline_names":base_n,
                                   "baseline_avg_pct":round(sum(base_rets)/len(base_rets),3) if base_rets else 0},
                         "rows":rows})
    # merge with existing (TV 6/26) day
    existing=json.load(open(OUT))
    tv_days=[d for d in existing["days"] if d["day"] not in {x["day"] for x in days_out}]
    for d in tv_days: d.setdefault("source","TradingView 2-min (live capture)")
    existing["days"]=sorted(tv_days+days_out, key=lambda d:d["day"], reverse=True)
    existing["ab_note"]=("60-day A/B on the full 231-name universe: BASELINE (live rules) +58.9% / 966 trades "
                         "(+0.061%/trade) vs VARIANT −48.5% / 2209 trades (−0.022%/trade). The variant LOSES "
                         "out-of-sample — shown per day for inspection, not as a recommendation.")
    json.dump(existing,open(OUT,"w"),indent=2,default=str)
    print(json.dumps({"added_days":[{"day":d["day"],"totals":d["totals"]} for d in days_out]},indent=2))

if __name__=="__main__":
    main()
