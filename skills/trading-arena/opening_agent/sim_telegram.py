#!/usr/bin/env python3
"""Reconstruct the REAL whole-market funnel picks the live scan posted to Telegram
(parsed from logs/opening_scan_cron.log), and replay the ones we have 2-min bars for
(IBKR broad cache). Long-only sweet arm, 45-min config. Injects a 'telegram' block
into logs/opening_sim_variant.json for the dashboard's Telegram tab.

This is the honest reality check: real whole-market picks, but bar coverage is sparse
(the live funnel is mostly small-caps not in our 231-name cache)."""
import os, sys, re, json, glob
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sim_variant_ibkr_days as V
V.SELLOFF_MIN, V.RR = 45, 3.0          # 45-min config for the replay
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "opening_sim_variant.json")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "opening_scan_cron.log")
CAP0, SLOTS = 1000.0, 5

HDR = re.compile(r"Opening Power — Top \d+</b>\s*<i>(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) ET")
ROW = re.compile(r"([🟢🔴⚪])\s*<b>#(\d+)\s+([A-Z][A-Z0-9.]*)</b>\s*—\s*<b>([\d.]+)</b>\s*\(([^)]+)\)")
DET = re.compile(r"^\s+(\w+)/(\w+)\s+tight\s+([\d.]+)\s+gap\s+([+-][\d.]+)%")

def parse_log():
    """Return {date_str: [picks]} using the LATEST pre-open run that day."""
    runs = {}                                       # (date,time) -> [picks]
    cur = None
    for line in open(LOG, errors="ignore"):
        h = HDR.search(line)
        if h:
            cur = (h.group(1), h.group(2)); runs.setdefault(cur, [])
            continue
        if cur is None: continue
        m = ROW.search(line)
        if m:
            emoji, rank, sym, score, tag = m.groups()
            runs[cur].append({"rank": int(rank), "sym": sym, "score": float(score),
                              "dir": tag.strip(), "emoji": emoji})
            continue
        d = DET.match(line)
        if d and runs[cur]:
            runs[cur][-1].update({"state": d.group(1), "loc": d.group(2),
                                  "tight": float(d.group(3)), "gap": float(d.group(4))})
    # keep the latest-time run per day, with picks
    by_day = {}
    for (dstr, tstr), picks in sorted(runs.items()):
        if picks: by_day[dstr] = picks                # later time overwrites earlier
    return by_day

def main():
    by_day = parse_log()
    # load bars: broad cache first, then the fresh telegram_cache (full pull of the
    # actual Telegram picks through 6/26) overrides/extends it.
    TGCACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "telegram_cache")
    cache = {}
    for cdir in (V.CACHE, TGCACHE):
        for p in glob.glob(os.path.join(cdir, "*.json")):
            try: cache[os.path.basename(p)[:-5]] = V.load(p)
            except Exception: pass
    days_out = []
    for dstr, picks in sorted(by_day.items()):
        day = date.fromisoformat(dstr)
        rows = []
        for pk in sorted(picks, key=lambda x: x["rank"]):
            sym = pk["sym"]; rec = {"rank": pk["rank"], "sym": sym, "gap": pk.get("gap"),
                                    "dir": pk["dir"], "state": pk.get("state", "?"),
                                    "in_cache": sym in cache, "armed": False, "ret_pct": None}
            if sym in cache:
                bars, s20, s200, byday = cache[sym]
                if day in byday:
                    r = V.sim_one(bars, s20, s200, byday[day], day, sym, "sweet")
                    if r and r.get("position_cost"):
                        rec["armed"] = True
                        rec["ret_pct"] = round(r["realized_pl"] / r["position_cost"] * 100, 3)
                        rec["arm_t"] = r["arm_t"]; rec["entry"] = r["entry"]
            rows.append(rec)
        days_out.append({"day": dstr, "n_picks": len(rows),
                         "n_cache": sum(1 for r in rows if r["in_cache"]),
                         "n_armed": sum(1 for r in rows if r["armed"]),
                         "picks": rows})
    # compound over replayable+armed picks (first-5-by-rank each day)
    cap = CAP0; curve = []
    for d in days_out:
        tr = [r for r in d["picks"] if r["armed"]][:SLOTS]
        slot = cap / SLOTS
        for r in tr: cap += slot * r["ret_pct"] / 100
        curve.append({"day": d["day"], "n_traded": len(tr), "capital": round(cap, 2)})
    # gate-value: passed-the-2-min (armed, gated) vs failed-but-traded-anyway (buy open, 45-min)
    from datetime import datetime as _dt, timedelta as _td
    def naive(bars, idxs, day):
        if not idxs: return None
        entry = bars[idxs[0]]["open"]; so = _dt.combine(day, V.OPEN_T, V.ET) + _td(minutes=45); ex = bars[idxs[0]]["close"]
        for i in idxs:
            ex = bars[i]["close"]
            if bars[i]["dt"] >= so: break
        return (ex - entry) / entry * 100 if entry else None
    passed_r, failed_r = [], []
    for d in days_out:
        day = date.fromisoformat(d["day"])
        for pk in d["picks"]:
            if pk["armed"]: passed_r.append(pk["ret_pct"])
            elif pk["in_cache"] and pk["sym"] in cache:
                bars, s20, s200, byday = cache[pk["sym"]]
                if day in byday:
                    nr = naive(bars, byday[day], day)
                    if nr is not None: failed_r.append(nr)
    avg = lambda r: round(sum(r) / len(r), 3) if r else None
    tel = {"days": days_out, "compound": {"start": CAP0, "end": round(cap, 2),
            "total_pct": round((cap / CAP0 - 1) * 100, 2), "curve": curve},
           "cache_size": len(cache),
           "gate": {"passed_n": len(passed_r), "passed_avg": avg(passed_r),
                    "passed_win": round(sum(1 for x in passed_r if x > 0) / len(passed_r) * 100, 1) if passed_r else 0,
                    "failed_n": len(failed_r), "failed_avg": avg(failed_r)},
           "totals": {"scan_days": len(days_out),
                      "total_picks": sum(d["n_picks"] for d in days_out),
                      "in_cache": sum(d["n_cache"] for d in days_out),
                      "armed_replayed": sum(d["n_armed"] for d in days_out)}}
    data = json.load(open(OUT)); data["telegram"] = tel
    json.dump(data, open(OUT, "w"), indent=2, default=str)
    t = tel["totals"]
    print(f"scan days: {t['scan_days']} | total picks: {t['total_picks']} | "
          f"in 231-cache: {t['in_cache']} | actually replayable (armed): {t['armed_replayed']}")
    print(f"compounded over replayable picks: $among {tel['compound']['end']} ({tel['compound']['total_pct']:+}%)")

if __name__ == "__main__":
    main()
