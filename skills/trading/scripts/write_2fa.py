#!/usr/bin/env python3
"""Write a 2FA code for the trading agent's Questrade web login.

Usage: python3 write_2fa.py 123456
"""
import sys
from pathlib import Path

CODE_FILE = Path("/home/tonygale/openclaw/state/questrade_2fa_code.txt")

if len(sys.argv) < 2:
    print("Usage: write_2fa.py <code>")
    sys.exit(1)

code = sys.argv[1].strip()
CODE_FILE.write_text(code)
print(f"2FA code '{code}' written. Trading agent will pick it up within 5 seconds.")
