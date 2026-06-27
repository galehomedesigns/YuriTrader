#!/usr/bin/env python3
"""Pass 4: day-level regime gate as a loss-minimizer. Skip whole days where the index tape
is down over the open (9:30->9:44). Test SPY/QQQ/IWM and a few thresholds on the recent window
(extends to full once regime_cache backfills)."""
import os, sys, glob
from datetime import date, datetime, time as dtime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_variant_ibkr_days as V

def load_idx(cdirs):
    idx={}
    for cdir in cdirs:
        for sym in ("SPY","QQQ","IWM"):
            p=os.path.join(cdir,sym+".json")
            if os.path.exists(p):
                try: idx[sym]=V.load(p)
                except: pass
    return idx

def early_ret(idx,sym,day,endmin=14):
    if sym not in idx: return None
    bars,_,_,byday=idx[sym]
    if day not in byday: return None
    ii=byday[day]; o=bars[ii[0]]["open"]
    end=datetime.combine(day,V.OPEN_T,V.ET)+timedelta(minutes=endmin); ex=bars[ii[0]]["close"]
    for i in ii:
        ex=bars[i]["close"]
        if bars[i]["dt"]>=end: break
    return (ex-o)/o*100 if o else None

_C=None
def _load():
    global _C
    if _C: return _C
    syms={}
    for p in glob.glob(os.path.join(V.CACHE,"*.json")):
        try: syms[os.path.basename(p)[:-5]]=V.load(p)
        except: pass
    ad=sorted({d for (_,_,_,bd) in syms.values() for d in bd})
    npr={d:sum(1 for (_,_,_,bd) in syms.values() if d in bd) for d in ad}
    _C=(syms,ad,npr); return _C

def trades(window):
    syms,ad,npr=_load()
    days=[d for d in ad if npr[d]>=150] if window=="full" else [d for d in ad if d.isoformat()>="2026-05-22"]
    out=[]
    for s,(bars,s20,s200,byday) in syms.items():
        dts=sorted(byday)
        for day in days:
            if day not in byday: continue
            pos=dts.index(day)
            if pos==0: continue
            idxs=byday[day]
            if len(idxs)<12: continue
            pclose=bars[byday[dts[pos-1]][-1]]["close"]; o=bars[idxs[0]]["open"]
            gap=(o-pclose)/pclose*100
            if not (V.GAP_MIN<=gap<=V.GAP_MAX) or o>V.MAX_PRICE or o<V.MIN_PRICE: continue
            r=V.sim_one(bars,s20,s200,idxs,day,s,"sweet")
            if not r or not r.get("position_cost"): continue
            out.append(dict(day=day,arm_t=r["arm_t"],ret=r["realized_pl"]/r["position_cost"]*100))
    return out

def comp(tr):
    byday={}
    for t in tr: byday.setdefault(t["day"],[]).append(t)
    cap=1000.0
    for day in sorted(byday):
        for p in sorted(byday[day],key=lambda x:(x["arm_t"] or "99:99"))[:5]:
            cap+=(cap/5)*p["ret"]/100
    return round((cap/1000-1)*100,2), len(byday)

if __name__=="__main__":
    idx=load_idx([os.path.join("logs","regime_cache"), os.path.join("logs","telegram_cache")])
    print("index syms loaded:", list(idx.keys()))
    for win in ("recent","full"):
        tr=trades(win)
        days=sorted({t["day"] for t in tr})
        cov=sum(1 for d in days if early_ret(idx,"SPY",d) is not None)
        b,nd=comp(tr)
        print(f"\n==== {win.upper()}  base COMP={b}% over {nd} days | SPY coverage {cov}/{len(days)} days ====")
        if cov < len(days)*0.8:
            print("  (insufficient SPY coverage for this window — skipping)"); continue
        for sym in ("SPY","QQQ","IWM"):
            for thr in (0.0, 0.1):
                kept=[t for t in tr if (early_ret(idx,sym,t["day"]) or -9) > thr]
                c,nk=comp(kept)
                ntr=len(kept)
                print(f"  skip days {sym} 9:30->9:44 <= {thr:+.1f}%:  trade {nk}/{nd} days, {ntr} trades -> COMP {c}%  (Δ {c-b:+.2f})")
