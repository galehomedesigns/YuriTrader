#!/usr/bin/env python3
"""invite_codes.py — generate / validate / redeem Gale Force Arena invitation codes.

Membership is invitation-only: you hand out codes, an applicant enters one at
registration, and it can be redeemed exactly once. This is a standalone, dependency-free
utility (local JSON store) that the member portal will call when it's built.

Store: state/remote_confirm/invites.json
Code:  GFA-XXXX-XXXX  (unambiguous alphabet — no 0/O/1/I/L)

Usage:
  invite_codes.py generate 5 [--note "spring batch"]
  invite_codes.py list [--unused]
  invite_codes.py validate GFA-XXXX-XXXX        # exit 0 if redeemable
  invite_codes.py redeem   GFA-XXXX-XXXX --user alice [--email a@x.com]
  invite_codes.py revoke   GFA-XXXX-XXXX
"""
import argparse
import datetime
import json
import os
import secrets
import sys

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "..", "state", "remote_confirm", "invites.json")
STORE = os.path.normpath(STORE)
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0 O 1 I L


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE))
        except Exception:
            pass
    return {"codes": []}


def _save(data):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    json.dump(data, open(STORE, "w"), indent=2)


def _new_code(existing):
    while True:
        c = "GFA-" + "".join(secrets.choice(ALPHABET) for _ in range(4)) + \
            "-" + "".join(secrets.choice(ALPHABET) for _ in range(4))
        if c not in existing:
            return c


def _find(data, code):
    code = code.strip().upper()
    for rec in data["codes"]:
        if rec["code"] == code:
            return rec
    return None


def cmd_generate(a):
    data = _load(); have = {r["code"] for r in data["codes"]}
    made = []
    for _ in range(a.n):
        c = _new_code(have); have.add(c)
        data["codes"].append({"code": c, "created": _now(), "note": a.note,
                              "status": "unused", "used_by": None, "used_at": None})
        made.append(c)
    _save(data)
    print(f"Generated {len(made)} invite code(s){' — '+a.note if a.note else ''}:")
    for c in made:
        print("  " + c)


def cmd_list(a):
    data = _load()
    rows = [r for r in data["codes"] if (not a.unused or r["status"] == "unused")]
    print(f"{'code':18} {'status':8} {'used_by':14} {'note'}")
    print("-" * 60)
    for r in rows:
        print(f"{r['code']:18} {r['status']:8} {str(r['used_by'] or ''):14} {r.get('note') or ''}")
    n = len(data["codes"]); u = sum(1 for r in data["codes"] if r["status"] == "unused")
    print(f"\n{n} total · {u} unused · {n-u} used/revoked")


def cmd_validate(a):
    rec = _find(_load(), a.code)
    if rec and rec["status"] == "unused":
        print("VALID — redeemable"); sys.exit(0)
    print("INVALID — " + ("not found" if not rec else rec["status"])); sys.exit(1)


def cmd_redeem(a):
    data = _load(); rec = _find(data, a.code)
    if not rec:
        sys.exit("redeem failed: code not found")
    if rec["status"] != "unused":
        sys.exit(f"redeem failed: code is {rec['status']}")
    rec["status"] = "used"; rec["used_by"] = a.user; rec["used_email"] = a.email; rec["used_at"] = _now()
    _save(data)
    print(f"Redeemed {rec['code']} -> {a.user}")


def cmd_revoke(a):
    data = _load(); rec = _find(data, a.code)
    if not rec:
        sys.exit("revoke failed: code not found")
    rec["status"] = "revoked"; _save(data)
    print(f"Revoked {rec['code']}")


def main():
    ap = argparse.ArgumentParser(description="Gale Force Arena invitation codes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("n", type=int); g.add_argument("--note", default=""); g.set_defaults(fn=cmd_generate)
    l = sub.add_parser("list"); l.add_argument("--unused", action="store_true"); l.set_defaults(fn=cmd_list)
    v = sub.add_parser("validate"); v.add_argument("code"); v.set_defaults(fn=cmd_validate)
    r = sub.add_parser("redeem"); r.add_argument("code"); r.add_argument("--user", required=True); r.add_argument("--email", default=None); r.set_defaults(fn=cmd_redeem)
    rv = sub.add_parser("revoke"); rv.add_argument("code"); rv.set_defaults(fn=cmd_revoke)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
