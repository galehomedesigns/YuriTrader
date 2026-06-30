#!/usr/bin/env python3
"""Opening-Power strategy PROFILES — a pre-market toggle between named rule sets.

One env var, `OPENING_STRATEGY_PROFILE`, selects a bundle of the underlying
`OPENING_*` knobs. Set it pre-market (in .env, or `profiles.py set <name>`) and
the day's scan + monitor run that strategy. `baseline` overrides NOTHING — it is
the existing live behaviour, untouched.

Why a bundle (not 8 separate edits): the live config is spread across knobs the
classifier (arm gate), engine (exit shape), and scan (gap band / time-stop) read
at startup. A profile sets them atomically so you can't half-switch.

Integration: each entry point calls `apply_to_env()` immediately after it loads
.env and BEFORE it imports classifier/engine — so every import-time default
(`classifier.DEFAULTS`, `advisory_monitor.CUTOFF_MIN`, …) sees the profile. This
module imports ONLY os/sys so importing it never triggers those reads early.

The three profiles map straight to the validated dashboard variants:
  baseline  — current live: TIGHT on · location by open · push-trail exit · gap 1–25% · 30-min
  sweet30   — "NEW SIM sweet-spot": TIGHT off · loc by close(>200) · 3R target · gap 0.5–4% · 30-min
  sweet45   — "NEW SIM 45-min":     same sweet-spot rules but gap 2–4% · 45-min hold
(sweet45 was the strongest in the 61-day A/B: +19.1% vs the 30-min sweet-spot's +10.2%.)
"""
import os
import sys

ENV_FILE = "/home/tonygale/openclaw/.env"
PROFILE_VAR = "OPENING_STRATEGY_PROFILE"
DEFAULT = "baseline"

# Each profile is a flat dict of OPENING_* overrides. `baseline` is empty by
# design: it leaves whatever .env already has, so existing live behaviour is
# byte-for-byte unchanged. Values are strings (they go straight into os.environ).
PROFILES = {
    "baseline": {},
    "sweet30": {
        "OPENING_REQUIRE_TIGHT": "false",     # TIGHT/coil gate OFF
        "OPENING_LOC_MODE": "close_slow",      # location = close > 200-SMA (not open vs band)
        "OPENING_EXIT_MODE": "target_3r",      # fixed 3R take-profit, no push-trail, no add
        "OPENING_TARGET_RR": "3.0",
        "OPENING_ENTRY_FRACTION": "1.0",       # enter the FULL slot at once (no half+add)
        "OPENING_SCAN_MIN_GAP_PCT": "0.5",
        "OPENING_SCAN_MAX_GAP_PCT": "4.0",
        "OPENING_SESSION_CUTOFF_MIN": "30",    # 30-min time-stop
    },
    "sweet45": {
        "OPENING_REQUIRE_TIGHT": "false",
        "OPENING_LOC_MODE": "close_slow",
        "OPENING_EXIT_MODE": "target_3r",
        "OPENING_TARGET_RR": "3.0",
        "OPENING_ENTRY_FRACTION": "1.0",
        "OPENING_SCAN_MIN_GAP_PCT": "2.0",     # tighter gap band — drops the smallest gappers
        "OPENING_SCAN_MAX_GAP_PCT": "4.0",
        "OPENING_SESSION_CUTOFF_MIN": "45",    # 45-min time-stop (the only other diff vs sweet30)
    },
    "sweet45ta": {                             # sweet45 + trend-align — best combo in the 61d A/B
        "OPENING_REQUIRE_TIGHT": "false",
        "OPENING_LOC_MODE": "close_slow",
        "OPENING_EXIT_MODE": "target_3r",
        "OPENING_TARGET_RR": "3.0",
        "OPENING_ENTRY_FRACTION": "1.0",
        "OPENING_SCAN_MIN_GAP_PCT": "2.0",
        "OPENING_SCAN_MAX_GAP_PCT": "4.0",
        "OPENING_SESSION_CUTOFF_MIN": "45",
        "OPENING_REQUIRE_TREND_ALIGN": "true",  # require SMA20>SMA200 at the arm bar (chop insurance)
    },
}

ORDER = ["baseline", "sweet30", "sweet45", "sweet45ta"]

ONE_LINER = {
    "baseline": "current live — TIGHT on · loc by open · push-trail · gap 1–25% · 30-min",
    "sweet30":  "sweet-spot — TIGHT off · loc by close · 3R target · gap 0.5–4% · 30-min",
    "sweet45":  "sweet-spot 45 — TIGHT off · loc by close · 3R target · gap 2–4% · 45-min",
    "sweet45ta": "BEST COMBO — sweet45 + trend-align (SMA20>200); IS+OOS-robust, +19.6%/61d",
}


def active_name():
    """The selected profile name (env wins; default 'baseline'). Unknown → baseline."""
    name = (os.environ.get(PROFILE_VAR) or DEFAULT).strip().lower()
    return name if name in PROFILES else DEFAULT


def resolve(name=None):
    """Return (name, overrides-dict) for the named (or active) profile."""
    name = (name or active_name()).strip().lower()
    if name not in PROFILES:
        name = DEFAULT
    return name, dict(PROFILES[name])


def apply_to_env(name=None, verbose=True):
    """Set this profile's OPENING_* overrides into os.environ (OVERRIDING any
    value .env / the shell already set — the profile is the higher-priority
    selection). MUST be called before classifier/engine are imported. baseline
    sets nothing. Returns the resolved profile name."""
    name, overrides = resolve(name)
    for k, v in overrides.items():
        os.environ[k] = str(v)
    os.environ[PROFILE_VAR] = name
    if verbose and overrides:
        print(f"[profile] {name}: " + " ".join(f"{k}={v}" for k, v in overrides.items()),
              file=sys.stderr)
    elif verbose:
        print(f"[profile] {name} (no overrides — using .env as-is)", file=sys.stderr)
    return name


def export_lines(name=None):
    """Shell `export K=V` lines for the active profile (for optional .sh wrappers)."""
    _, overrides = resolve(name)
    return "\n".join(f"export {k}={v}" for k, v in overrides.items())


def set_profile(name, path=ENV_FILE):
    """Flip the single OPENING_STRATEGY_PROFILE line in .env (surgical — touches
    only that line, appends if absent). Used by the `set` CLI for the pre-market
    toggle. Does NOT write the bundle into .env; the bundle is applied at runtime."""
    name = name.strip().lower()
    if name not in PROFILES:
        raise SystemExit(f"unknown profile '{name}'. choose one of: {', '.join(ORDER)}")
    line = f"{PROFILE_VAR}={name}\n"
    try:
        lines = open(path).readlines()
    except FileNotFoundError:
        lines = []
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith(PROFILE_VAR + "="):
            out.append(line); found = True
        else:
            out.append(ln)
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(line)
    open(path, "w").writelines(out)
    return name


def _print_status():
    name = active_name()
    print(f"active profile: {name}  —  {ONE_LINER[name]}")
    print()
    for n in ORDER:
        mark = "→" if n == name else " "
        print(f" {mark} {n:9} {ONE_LINER[n]}")
        for k, v in PROFILES[n].items():
            print(f"       {k}={v}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "show"
    if cmd in ("show", "status", "list"):
        _print_status()
    elif cmd == "export":                         # for `eval "$(profiles.py export)"`
        print(export_lines())
    elif cmd == "set":
        if len(argv) < 3:
            raise SystemExit("usage: profiles.py set <baseline|sweet30|sweet45>")
        n = set_profile(argv[2])
        print(f"set {PROFILE_VAR}={n} in {ENV_FILE}")
        print(f"  {ONE_LINER[n]}")
    else:
        raise SystemExit("usage: profiles.py [show | set <name> | export]")


if __name__ == "__main__":
    main(sys.argv)
