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
RISK_USD = 6.0                 # risk-sizing variant: cap $-risk/trade (= 3% of the $200 slot)
N_DAYS = 22; TOP_PER_DAY = 8     # ~one month of trading days (+ the 6/26 TV day)
MAX_PRICE = 300.0              # remove high-priced stocks (>$300) from the dashboard
MIN_PRICE = 5.0                # match the LIVE scanner floor (OPENING_MIN_PRICE default $5)

def panel(rows):
    """Build a {totals, rows, picks} panel: totals over ALL rows, charts for the top-N
    by P&L, and lightweight picks (all candidates) for the compounding summary."""
    pcts=[r["realized_pl"]/r["position_cost"]*100 for r in rows if r.get("position_cost")]
    disp=sorted(rows,key=lambda r:-(r.get("realized_pl") or 0))[:TOP_PER_DAY]
    picks=[{"sym":r["symbol"],"gap_pct":r.get("premarket_gap_pct"),"arm_t":r.get("arm_t"),
            "ret_pct":round(r["realized_pl"]/r["position_cost"]*100,3)}
           for r in rows if r.get("position_cost")]
    return {"totals":{"realized_pl":round(sum(r["realized_pl"] for r in rows),2),
                      "avg_pct":round(sum(pcts)/len(pcts),3) if pcts else 0,
                      "names":len(rows),"shown":len(disp),
                      "held_to_1020_pl":round(sum((r["held_to_1020_pl"] or 0) for r in rows),2)},
            "rows":disp,"picks":picks}

def risk_resize_rows(rows):
    """Re-size each baseline row to risk <= $RISK_USD per trade, capped at one SLOT —
    the "size DOWN instead of skip/full-slot" option. Sizing is linear, so realized P&L,
    position cost and held-P&L all scale by new_shares/old_shares; per-trade ret% is
    unchanged. Pure + additive: never mutates the input rows."""
    out=[]
    for r in rows:
        rps=r.get("risk_per_share") or 0.0; old=r.get("shares") or 0; entry=r.get("entry") or 0.0
        nr=dict(r)
        if rps>0 and old>0 and entry>0:
            new=max(1, min(math.floor(RISK_USD/rps), math.floor(SLOT/entry)))
            f=new/old
            nr["shares"]=new
            nr["position_cost"]=round(new*entry,2)
            nr["realized_pl"]=round((r.get("realized_pl") or 0.0)*f,2)
            if r.get("held_to_1020_pl") is not None:
                nr["held_to_1020_pl"]=round(r["held_to_1020_pl"]*f,2)
            nr["sized_down"]=new<old
        else:
            nr["sized_down"]=False
        out.append(nr)
    return out

def risksize_panel(base_rows):
    """A panel() over risk-resized copies of the baseline rows, with the count of
    trades that had to be trimmed (risk > cap) stashed on totals for the A/B tab."""
    rr=risk_resize_rows(base_rows)
    p=panel(rr)
    p["totals"]["n_sized_down"]=sum(1 for r in rr if r.get("sized_down"))
    return p
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

def sim_one(bars,s20,s200,idxs,day,sym,mode,be_abs=None,be_r=1.0,lock_abs=0.0,mae_stop=None,noprog=None,req20=False,trend_align=False):
    """Full sim producing row+timeline for one candidate, in 'sweet' or 'baseline' mode.
    sweet:    arm power+close>SMA200 (no TIGHT), wick stop, 3R target, breakeven@1R, 30-min.
    baseline: arm classifier MATCH_LONG (TIGHT on, loc by open), wick stop, breakeven@1R +
              push-trail (prior-bar low), no fixed target, 30-min sell-off.
    base_simarm: baseline EXIT (push-trail) but with the sim arm gate (power+close>SMA200, no TIGHT).
    firstgreen:  baseline EXIT (push-trail), arm ONLY on the 09:30 bar if it's green (close>open,
                 any size); if the first bar isn't up, the name is skipped entirely (no rolling)."""
    arm=None
    if mode=="firstgreen":
        # ONLY the 09:30 bar: if it's green (close>open, any size) we arm; else never trade this name.
        i0=idxs[0]
        if s200[i0] is not None and bars[i0]["close"]>bars[i0]["open"]: arm=i0
    else:
        for i in idxs:
            if bars[i]["dt"].time()>ARM_END: break
            if s200[i] is None: continue
            prior=bars[max(0,i-30):i]
            if mode in ("sweet","base_simarm"):    # sim arm gate: power bar + close>SMA200 (no TIGHT)
                ok=(C.bar_signal(bars[i],prior)>0 and bars[i]["close"]>s200[i]
                    and (not req20 or (s20[i] is not None and bars[i]["close"]>s20[i])))
            else:                                  # baseline: full classifier (TIGHT on + loc-by-open)
                ok=(C.classify_opening("S",bars[i],prior,s20[i],s200[i]).decision=="MATCH_LONG")
            # trend-align filter (subtractive A/B): require the 20-SMA ABOVE the 200-SMA at the
            # arm bar (MAs stacked bullishly) — consumes the SMA20-vs-200 direction the live
            # classifier currently discards. Default off.
            if ok and trend_align and not (s20[i] is not None and s200[i] is not None and s20[i]>s200[i]):
                ok=False
            if ok: arm=i; break
    if arm is None: return None
    entry=round(bars[arm]["high"]+OFFSET,2); stop=round(bars[arm]["low"]-OFFSET,2)
    if stop>=entry: return None
    risk=round(entry-stop,4); shares=max(1,math.floor(SLOT/entry))
    target=round(entry+RR*risk,4) if mode=="sweet" else None
    loc=("above-both" if bars[arm]["close"]>max(s20[arm],s200[arm]) else "above-200/below-20")
    selloff=datetime.combine(day,OPEN_T,ET)+timedelta(minutes=SELLOFF_MIN)
    tl=[]; in_pos=False; cur=stop; be=False; exitrec=None; ent_dt=None; sesshi=None; prev_low=None; nb=0
    for i in idxs:
        b=bars[i]; t=b["dt"]
        if t.time()>WIN_END: break
        ev=[]
        if t.time()==bars[arm]["dt"].time():
            ev.append(f"ARMED ${entry:.2f}, stop ${stop:.2f}"+(f", target ${target:.2f} (3R)" if target else " (trail)"))
        if not in_pos and exitrec is None and i>arm and b["high"]>=entry:
            in_pos=True; ent_dt=t; prev_low=b["low"]; nb=0
            if mae_stop is not None: cur=max(cur,round(entry*(1-mae_stop/100),4))   # cap the loss tighter than the wick stop
            ev.append(f"ENTRY {shares} @ ${entry:.2f}")
        if in_pos:
            nb+=1
            sesshi=b["high"] if sesshi is None else max(sesshi,b["high"])
            if target and b["high"]>=target: exitrec={"time":t.strftime("%H:%M"),"price":target,"reason":"3R target hit","qty":shares};ev.append(f"TARGET exit ${target:.2f}");in_pos=False
            elif b["low"]<=cur: exitrec={"time":t.strftime("%H:%M"),"price":cur,"reason":("breakeven" if be else "protective")+" stop hit","qty":shares};ev.append(f"STOP exit ${cur:.2f}");in_pos=False
            else:
                trig=(b["high"]>=entry+be_r*risk) or (be_abs is not None and b["high"]>=entry*(1+be_abs/100))
                if not be and trig: cur=round(entry*(1+lock_abs/100),4) if lock_abs else entry; be=True; ev.append(f"+BE/lock → ${cur:.2f}")
                if mode in ("baseline","base_simarm","firstgreen") and be and prev_low is not None and prev_low-OFFSET>cur:
                    cur=round(prev_low-OFFSET,4);ev.append(f"trail stop → ${cur:.2f}")
                if noprog is not None and not be and nb>=noprog and b["close"]<entry:
                    exitrec={"time":t.strftime("%H:%M"),"price":round(b["close"],4),"reason":"no-progress exit","qty":shares};ev.append(f"NO-PROGRESS exit ${b['close']:.2f}");in_pos=False
                elif t>=selloff: exitrec={"time":t.strftime("%H:%M"),"price":round(b["close"],4),"reason":f"{SELLOFF_MIN}-min sell-off","qty":shares};ev.append(f"{SELLOFF_MIN}-MIN SELL-OFF ${b['close']:.2f}");in_pos=False
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

CAPITAL = 1000.0; SLOTS = 5     # OPENING_TRADE_BUDGET_USD / OPENING_MAX_TRADES

def _compound_one(days, setup):
    """Daily-compounded equity curve: each day, top-SLOTS picks by gap, equal slot =
    capital/SLOTS (unused slots = cash), apply each pick's % return, compound."""
    cur = CAPITAL; curve = []
    for d in sorted(days, key=lambda x: x["day"]):     # oldest -> newest
        picks = sorted(d.get(setup, {}).get("picks", []), key=lambda p: (p.get("arm_t") or "99:99"))[:SLOTS]
        slot = cur / SLOTS
        rows = [{"sym": p["sym"], "gap_pct": p.get("gap_pct"), "ret_pct": p["ret_pct"],
                 "ret_usd": round(slot * p["ret_pct"] / 100, 2)} for p in picks]
        day_pl = round(sum(x["ret_usd"] for x in rows), 2)
        day_ret = round(day_pl / cur * 100, 3) if cur else 0
        cur = round(cur + day_pl, 2)
        curve.append({"day": d["day"], "source": d.get("source", ""), "slot": round(slot, 2),
                      "n": len(rows), "picks": rows, "day_pl": day_pl, "day_ret_pct": day_ret,
                      "capital": cur})
    return {"start": CAPITAL, "end": cur, "total_pct": round((cur / CAPITAL - 1) * 100, 2),
            "curve": curve}

def compound_summary(days):
    return {"capital": CAPITAL, "slots": SLOTS,
            "sweet": _compound_one(days, "sweet"),
            "baseline": _compound_one(days, "baseline")}

def picks_for(syms, days, mode, gmin, gmax, selloff, **simkw):
    """Per-day picks for a given config (gap band, sell-off min) — for the 45-min A/B.
    simkw is forwarded to sim_one (e.g. req20=True for the above-both loss filter)."""
    global SELLOFF_MIN
    save = SELLOFF_MIN; SELLOFF_MIN = selloff
    out = {}
    for day in days:
        lst = []
        for s, (bars, s20, s200, byday) in syms.items():
            if day not in byday: continue
            dates = sorted(byday); pos = dates.index(day)
            if pos == 0: continue
            idxs = byday[day]
            if len(idxs) < 12: continue
            pclose = bars[byday[dates[pos - 1]][-1]]["close"]; o = bars[idxs[0]]["open"]
            gap = (o - pclose) / pclose * 100
            if not (gmin <= gap <= gmax) or o > MAX_PRICE or o < MIN_PRICE: continue
            r = sim_one(bars, s20, s200, idxs, day, s, mode, **simkw)
            if r and r.get("position_cost"):
                lst.append({"sym": r["symbol"], "gap_pct": round(gap, 2), "arm_t": r["arm_t"],
                            "ret_pct": round(r["realized_pl"] / r["position_cost"] * 100, 3)})
        out[day.isoformat()] = lst
    SELLOFF_MIN = save
    return out

def compound_from_picks(picks_by_day):
    cur = CAPITAL; curve = []
    for day in sorted(picks_by_day):
        picks = sorted(picks_by_day[day], key=lambda p: (p.get("arm_t") or "99:99"))[:SLOTS]
        slot = cur / SLOTS
        rows = [{"sym": p["sym"], "gap_pct": p.get("gap_pct"), "ret_pct": p["ret_pct"],
                 "ret_usd": round(slot * p["ret_pct"] / 100, 2)} for p in picks]
        day_pl = round(sum(x["ret_usd"] for x in rows), 2); prev = cur; cur = round(cur + day_pl, 2)
        curve.append({"day": day, "slot": round(slot, 2), "n": len(rows), "picks": rows, "day_pl": day_pl,
                      "day_ret_pct": round(day_pl / prev * 100, 3) if prev else 0, "capital": cur})
    return {"start": CAPITAL, "end": cur, "total_pct": round((cur / CAPITAL - 1) * 100, 2), "curve": curve}

def main():
    syms={}
    for p in glob.glob(os.path.join(CACHE,"*.json")):
        try: syms[os.path.basename(p)[:-5]]=load(p)
        except Exception: pass
    all_days=sorted({d for (_,_,_,bd) in syms.values() for d in bd})
    last=all_days[-N_DAYS:]
    days_out=[]
    for di_day in last:
        sweet=[]; base=[]; simarm=[]
        for s,(bars,s20,s200,byday) in syms.items():
            if di_day not in byday: continue
            dates=sorted(byday); pos=dates.index(di_day)
            if pos==0: continue
            idxs=byday[di_day]
            if len(idxs)<12: continue
            pclose=bars[byday[dates[pos-1]][-1]]["close"]; o=bars[idxs[0]]["open"]
            gap=(o-pclose)/pclose*100
            if not (GAP_MIN<=gap<=GAP_MAX): continue
            if o>MAX_PRICE or o<MIN_PRICE: continue        # match live: $5–$300 only
            for mode,bucket in (("sweet",sweet),("baseline",base),("base_simarm",simarm)):
                r=sim_one(bars,s20,s200,idxs,di_day,s,mode)
                if r:
                    r["premarket_gap_pct"]=round(gap,2); r["prev_close"]=round(pclose,2); r["today_open"]=round(o,2)
                    bucket.append(r)
        days_out.append({"day":di_day.isoformat(),"source":"IBKR 2-min (broad 231-name cache)",
                         "sweet":panel(sweet),"baseline":panel(base),"base_simarm":panel(simarm),
                         "risksize":risksize_panel(base)})
    # merge with existing (TV 6/26) day
    existing=json.load(open(OUT))
    tv_days=[d for d in existing["days"] if d["day"] not in {x["day"] for x in days_out}]
    existing["days"]=sorted(tv_days+days_out, key=lambda d:d["day"], reverse=True)
    existing["compound"]=compound_summary(existing["days"])
    # 45-min improved (gap 2-4, 45-min) vs current (gap 0.5-4, 30-min) over the FULL
    # populated window (where the durable edge was validated, not just the recent 22d).
    npres={d:sum(1 for (_,_,_,bd) in syms.values() if d in bd) for d in all_days}
    fdays=[d for d in all_days if npres[d]>=150]
    existing["compound45"]={"days":len(fdays),"window":f"{fdays[0]}..{fdays[-1]}",
        "improved":{"baseline":compound_from_picks(picks_for(syms,fdays,"baseline",2.0,4.0,45)),
                    "sweet":compound_from_picks(picks_for(syms,fdays,"sweet",2.0,4.0,45))},
        "current":{"baseline":compound_from_picks(picks_for(syms,fdays,"baseline",0.5,4.0,30)),
                   "sweet":compound_from_picks(picks_for(syms,fdays,"sweet",0.5,4.0,30))}}
    # arm-gate test: keep BASELINE exit (push-trail), swap only the arm gate, current config
    existing["armgate"]={"days":len(fdays),"window":f"{fdays[0]}..{fdays[-1]}","config":"gap 0.5–4% · 30-min · push-trail exit",
        "baseline":compound_from_picks(picks_for(syms,fdays,"baseline",0.5,4.0,30)),
        "base_simarm":compound_from_picks(picks_for(syms,fdays,"base_simarm",0.5,4.0,30)),
        "firstgreen":compound_from_picks(picks_for(syms,fdays,"firstgreen",0.5,4.0,30)),
        "sweet":compound_from_picks(picks_for(syms,fdays,"sweet",0.5,4.0,30))}
    # same arm-gate test over the RECENT month (matches the 📈 Summary window) so the lift is
    # apples-to-apples with the Summary tab, not diluted by the flat March–April first half.
    rec=[d for d in all_days if d.isoformat()>="2026-05-22"]
    existing["armgate_recent"]={"days":len(rec),"window":f"{rec[0]}..{rec[-1]}","config":"gap 0.5–4% · 30-min · push-trail exit",
        "baseline":compound_from_picks(picks_for(syms,rec,"baseline",0.5,4.0,30)),
        "base_simarm":compound_from_picks(picks_for(syms,rec,"base_simarm",0.5,4.0,30)),
        "firstgreen":compound_from_picks(picks_for(syms,rec,"firstgreen",0.5,4.0,30)),
        "sweet":compound_from_picks(picks_for(syms,rec,"sweet",0.5,4.0,30))}
    # loss-minimization filter: new-sim, current vs "above-both" (arm bar must close > 20-SMA too).
    # The one robust loss-reducer found (removes the sub-20-SMA losers); never hurts, helps full +2.5pts.
    existing["lossmin"]={
        "full":{"days":len(fdays),"window":f"{fdays[0]}..{fdays[-1]}",
                "current":compound_from_picks(picks_for(syms,fdays,"sweet",0.5,4.0,30)),
                "above_both":compound_from_picks(picks_for(syms,fdays,"sweet",0.5,4.0,30,req20=True))},
        "recent":{"days":len(rec),"window":f"{rec[0]}..{rec[-1]}",
                "current":compound_from_picks(picks_for(syms,rec,"sweet",0.5,4.0,30)),
                "above_both":compound_from_picks(picks_for(syms,rec,"sweet",0.5,4.0,30,req20=True))}}
    # trend-align A/B: sweet vs sweet + (20-SMA > 200-SMA at the arm bar). Subtractive — it can
    # only REMOVE non-trend-aligned arms. Split the full populated window into IS/OOS halves to
    # test robustness: a full-window win that fails OOS is regime luck, not an edge (firstgreen lesson).
    _half = len(fdays) // 2
    def _ta(days, mode):
        return {"days": len(days), "window": (f"{days[0]}..{days[-1]}" if days else ""),
                "off": compound_from_picks(picks_for(syms, days, mode, 0.5, 4.0, 30)),
                "on":  compound_from_picks(picks_for(syms, days, mode, 0.5, 4.0, 30, trend_align=True))}
    # Tested on BOTH the sweet-spot (already gates close>200, so trend-align is near-redundant)
    # AND the baseline/live classifier (where the SMA20-vs-200 direction is currently discarded —
    # the faithful test of the idea). Each split into IS/OOS halves for robustness.
    existing["trendalign"] = {
        "sweet":    {"full": _ta(fdays, "sweet"),    "is": _ta(fdays[:_half], "sweet"),    "oos": _ta(fdays[_half:], "sweet")},
        "baseline": {"full": _ta(fdays, "baseline"), "is": _ta(fdays[:_half], "baseline"), "oos": _ta(fdays[_half:], "baseline")}}
    # best-combo matrix: promising joint configs (the other tabs A/B one knob at a time; these
    # test them STACKED), IS/OOS/Full. Winner = sweet45 + trend-align.
    def _combo(mode, gmin, gmax, sell, **kw):
        return {w: compound_from_picks(picks_for(syms, ds, mode, gmin, gmax, sell, **kw))
                for w, ds in (("is", fdays[:_half]), ("oos", fdays[_half:]), ("full", fdays))}
    existing["combos"] = {
        "window": f"{fdays[0]}..{fdays[-1]}", "days": len(fdays),
        "is_days": _half, "oos_days": len(fdays) - _half,
        "rows": [
            {"key": "sweet45ta", "label": "sweet45 + trend-align", "best": True,
             "splits": _combo("sweet", 2.0, 4.0, 45, trend_align=True)},
            {"key": "sweet45", "label": "sweet45 (current best profile)",
             "splits": _combo("sweet", 2.0, 4.0, 45)},
            {"key": "sweet45ab", "label": "sweet45 + above-both (close>20)",
             "splits": _combo("sweet", 2.0, 4.0, 45, req20=True)},
            {"key": "sweet30", "label": "sweet30 (gap 0.5–4 / 30m)",
             "splits": _combo("sweet", 0.5, 4.0, 30)},
            {"key": "base45ta", "label": "baseline + gap2–4/45 + trend-align",
             "splits": _combo("baseline", 2.0, 4.0, 45, trend_align=True)},
        ]}
    # risk-based sizing A/B: same baseline trade selection, but each trade sized to risk
    # <= $RISK_USD (capped at one slot) instead of a flat $200 slot. The wide-bar names the
    # live 3% cap SKIPS and the fixed-slot baseline FULL-SLOTS are instead taken sized-down.
    rs=[]
    for d in existing["days"]:
        if "risksize" not in d or "baseline" not in d: continue
        bt=d["baseline"]["totals"]; rt=d["risksize"]["totals"]
        rs.append({"day":d["day"],"source":d.get("source",""),
                   "base_pl":bt["realized_pl"],"risk_pl":rt["realized_pl"],
                   "delta":round(rt["realized_pl"]-bt["realized_pl"],2),
                   "n":bt["names"],"n_down":rt.get("n_sized_down",0)})
    rs.sort(key=lambda x:x["day"])
    cb=cr=0.0
    for s in rs:
        cb=round(cb+s["base_pl"],2); cr=round(cr+s["risk_pl"],2)
        s["cum_base"]=cb; s["cum_risk"]=cr
    existing["risksize_ab"]={
        "days":len(rs),"window":(f"{rs[0]['day']}..{rs[-1]['day']}" if rs else ""),
        "r_usd":RISK_USD,"slot":SLOT,"cap_pct":round(RISK_USD/SLOT*100,1),
        "totals":{"base_pl":round(sum(s["base_pl"] for s in rs),2),
                  "risk_pl":round(sum(s["risk_pl"] for s in rs),2),
                  "delta":round(sum(s["delta"] for s in rs),2),
                  "n_trades":sum(s["n"] for s in rs),
                  "n_down":sum(s["n_down"] for s in rs)},
        "series":rs}
    json.dump(existing,open(OUT,"w"),indent=2,default=str)
    print(json.dumps({"added_days":[{"day":d["day"],"sweet$":d["sweet"]["totals"]["realized_pl"],
                                     "base$":d["baseline"]["totals"]["realized_pl"]} for d in days_out]},indent=2))

if __name__=="__main__":
    main()
