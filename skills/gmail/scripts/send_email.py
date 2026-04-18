#!/usr/bin/env python3
"""Send email from Gmail via SMTP."""

import argparse
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def main():
    parser = argparse.ArgumentParser(description="Send Gmail email")
    parser.add_argument("--to", required=True, help="Recipient email")
    parser.add_argument("--cc", default="", help="CC recipients (comma-separated)")
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--body", required=True, help="Email body")
    parser.add_argument("--html", action="store_true", help="Send as HTML")
    parser.add_argument("--confirmed", action="store_true", help="User has confirmed sending")
    args = parser.parse_args()

    # SECURITY: Require explicit --confirmed flag
    if not args.confirmed:
        print("⚠️  EMAIL DRAFT — NOT SENT. User approval required.")
        print(f"To: {args.to}")
        if args.cc:
            print(f"CC: {args.cc}")
        print(f"Subject: {args.subject}")
        print(f"Body:\n{args.body}")
        print("\n👉 Ask the user to approve, then re-run with --confirmed to send.")
        sys.exit(0)

    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        print("Error: GMAIL_USER and GMAIL_APP_PASSWORD must be set", file=sys.stderr)
        sys.exit(1)

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = args.to
    if args.cc:
        msg["Cc"] = args.cc
    msg["Subject"] = args.subject

    content_type = "html" if args.html else "plain"
    msg.attach(MIMEText(args.body, content_type))

    recipients = [args.to]
    if args.cc:
        recipients.extend([r.strip() for r in args.cc.split(",")])

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(user, recipients, msg.as_string())

    print(f"✅ Email sent to {args.to}" + (f" (cc: {args.cc})" if args.cc else ""))


if __name__ == "__main__":
    main()
