#!/usr/bin/env python3
"""Loss-minimization sweep. Re-run EVERY new-sim trade under candidate loss-cut rules and
measure win%, loss pool, avg, and compounded ($1000/5 first-5-by-arm) on BOTH windows.
Default params == current behaviour (sanity-checked)."""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_variant_ibkr_days as V

_CACHE = None
def _load():
    global _CACHE
    if _CACHE is None:
        syms = {}
        for p in glob.glob(os.path.join(V.CACHE, "*.json")):
            try: syms[os.path.basename(p)[:-5]] = V.load(p)
            except Exception: pass
        adays = sorted({d for (_,_,_,bd) in syms.values() for d in bd})
        npres = {d: sum(1 for (_,_,_,bd) in syms.values() if d in bd) for d in adays}
        _CACHE = (syms, adays, npres)
    return _CACHE

def trades(window, **params):
    syms, adays, npres = _load()
    days = [d for d in adays if npres[d] >= 150] if window=="full" else [d for d in adays if d.isoformat()>="2026-05-22"]
    out = []
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
            r=V.sim_one(bars,s20,s200,idxs,day,s,"sweet",**params)
            if not r or not r.get("position_cost"): continue
            out.append(dict(day=day.isoformat(),arm_t=r["arm_t"],
                ret=r["realized_pl"]/r["position_cost"]*100,reason=(r["exit"] or {}).get("reason","")))
    return out

def metrics(tr):
    n=len(tr); w=[t for t in tr if t["ret"]>0]; l=[t for t in tr if t["ret"]<=0]
    # compound: per day, first-5-by-arm
    byday={}
    for t in tr: byday.setdefault(t["day"],[]).append(t)
    cap=1000.0
    for day in sorted(byday):
        picks=sorted(byday[day],key=lambda x:(x["arm_t"] or "99:99"))[:5]; slot=cap/5
        for p in picks: cap+=slot*p["ret"]/100
    return dict(n=n, win=round(len(w)/n*100,1) if n else 0,
                avg=round(sum(t["ret"] for t in tr)/n,3) if n else 0,
                losspool=round(sum(t["ret"] for t in l),1),
                avgloss=round(sum(t["ret"] for t in l)/max(1,len(l)),3),
                comp=round((cap/1000-1)*100,2))

CANDS = {
  "CURRENT (be@1R)":            dict(),
  "BE abs +0.3%":              dict(be_abs=0.3),
  "BE abs +0.5%":              dict(be_abs=0.5),
  "BE abs +0.75%":             dict(be_abs=0.75),
  "BE abs +1.0%":              dict(be_abs=1.0),
  "BE +0.5R":                  dict(be_r=0.5),
  "BE +0.33R":                 dict(be_r=0.33),
  "lock +0.2% @ +0.5%":        dict(be_abs=0.5, lock_abs=0.2),
  "MAE cap 1.5%":              dict(mae_stop=1.5),
  "MAE cap 2.0%":              dict(mae_stop=2.0),
  "no-progress 3 bars":        dict(noprog=3),
  "no-progress 5 bars":        dict(noprog=5),
}

if __name__ == "__main__":
    for win in ("full","recent"):
        print(f"\n================ {win.upper()} window ================")
        print(f"{'rule':24} {'n':>4} {'win%':>6} {'avg%':>7} {'avgLoss':>8} {'lossPool':>9} {'COMPND':>8}")
        for name,p in CANDS.items():
            m=metrics(trades(win,**p))
            print(f"{name:24} {m['n']:>4} {m['win']:>6} {m['avg']:>7} {m['avgloss']:>8} {m['losspool']:>9} {m['comp']:>7}%")
