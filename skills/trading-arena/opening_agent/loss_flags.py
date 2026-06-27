#!/usr/bin/env python3
"""Pass 3: selection-flag screen. For each entry-time feature, split trades into
flagged/kept and report avg-ret + COMPOUND of each, plus the compound if we SKIP the
flagged subset. A good flag => flagged subset is net-negative AND skipping lifts compound."""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_variant_ibkr_days as V

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
            sm20=r["sma20"]; sm200=r["sma200"]; e=r["entry"]
            out.append(dict(day=day.isoformat(),arm_t=r["arm_t"],ret=r["realized_pl"]/r["position_cost"]*100,
                gap=r["premarket_gap_pct"] if "premarket_gap_pct" in r else round(gap,2),
                risk=r["risk_pct"],loc=r["loc"],
                ext20=round((e-sm20)/sm20*100,2) if sm20 else 0,     # entry % above 20-SMA
                ext200=round((e-sm200)/sm200*100,2) if sm200 else 0, # entry % above 200-SMA
                sep=round((sm20-sm200)/sm200*100,2) if sm200 else 0))# 20-vs-200 separation
    return out

def comp(tr):
    byday={}
    for t in tr: byday.setdefault(t["day"],[]).append(t)
    cap=1000.0
    for day in sorted(byday):
        for p in sorted(byday[day],key=lambda x:(x["arm_t"] or "99:99"))[:5]:
            cap+=(cap/5)*p["ret"]/100
    return round((cap/1000-1)*100,2)

def stat(tr):
    n=len(tr); return (n, round(sum(t["ret"] for t in tr)/n,3) if n else 0,
                       round(sum(1 for t in tr if t["ret"]>0)/n*100,1) if n else 0)

FLAGS = [
  ("arm late >9:34",    lambda t: t["arm_t"]>"09:34"),
  ("arm late >9:36",    lambda t: t["arm_t"]>"09:36"),
  ("arm late >9:38",    lambda t: t["arm_t"]>"09:38"),
  ("gap <1%",           lambda t: t["gap"]<1.0),
  ("gap <1.5%",         lambda t: t["gap"]<1.5),
  ("gap >3%",           lambda t: t["gap"]>3.0),
  ("risk >4%",          lambda t: t["risk"]>4.0),
  ("risk >3%",          lambda t: t["risk"]>3.0),
  ("risk <1.5%",        lambda t: t["risk"]<1.5),
  ("below 20-SMA",      lambda t: t["loc"]!="above-both"),
  ("ext>20-SMA >2%",    lambda t: t["ext20"]>2.0),
  ("ext>20-SMA >3%",    lambda t: t["ext20"]>3.0),
  ("ext>200 >5%",       lambda t: t["ext200"]>5.0),
  ("sep<0 (20<200)",    lambda t: t["sep"]<0),
]

if __name__=="__main__":
    for win in ("full","recent"):
        tr=trades(win); base=comp(tr); n0,a0,w0=stat(tr)
        print(f"\n========= {win.upper()}  base: n={n0} avg={a0} win={w0}% COMP={base}% =========")
        print(f"{'flag':18} {'#flag':>5} {'flagAvg':>8} {'flagWin':>7} {'keptAvg':>8} | {'COMP if SKIP':>12} {'Δvs base':>8}")
        for name,fn in FLAGS:
            fl=[t for t in tr if fn(t)]; kp=[t for t in tr if not fn(t)]
            if not fl: continue
            nf,af,wf=stat(fl); nk,ak,wk=stat(kp); cs=comp(kp)
            print(f"{name:18} {nf:>5} {af:>8} {wf:>7} {ak:>8} | {cs:>11}% {cs-base:>+8.2f}")
