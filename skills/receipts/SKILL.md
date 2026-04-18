---
name: receipts
description: Process receipts from any source — Telegram photos, Google Drive uploads, email receipts, or manual entry. Analyze with Gemini vision/AI and store in Excel per CRA categories. Use when Tony sends a photo, mentions an expense, asks about receipts/spending, or wants to scan email for receipts.
---

# Receipt Processing System

Processes receipt photos and categorizes them into an Excel spreadsheet compliant with Canadian accounting standards (CRA).

## CRITICAL FILE LOCATIONS — DO NOT ASK TONY FOR THESE
- **Excel spreadsheet:** `gdrive:Accountant/Expenses_2026.xlsx`
- **Local copy:** `/data/.openclaw/workspace/receipts/Expenses_2026.xlsx`
- **Incoming receipts:** `/data/.openclaw/workspace/receipts/incoming/`
- **Google Drive receipts:** `gdrive:Receipts/`
- These paths are HARDCODED in the scripts. Never ask Tony where the file is.

## Auto-Detection (Telegram Photos)

**When Tony sends a photo via Telegram**, check if it looks like a receipt. If it does:

1. Save the image to `/data/.openclaw/workspace/receipts/incoming/`
2. Run: `python3 /home/tonygale/openclaw/skills/receipts/scripts/process_single.py /path/to/image.jpg`
3. Report the results back to Tony

The script will:
- **If readable:** Extract all data and report: vendor, date, total, category, tax breakdown. Ask Tony to confirm before saving to the spreadsheet.
- **If blurry/unreadable:** Tell Tony the image quality is poor and ask for a retake. Be specific about what's unreadable (e.g. "I can see the vendor name but the total is blurry").
- **If not a receipt:** Tell Tony it doesn't appear to be a receipt and ask what it is.

## Two Input Methods

### Method 1: Telegram (preferred)
Tony sends a photo directly to Yuri on Telegram. Yuri auto-detects and processes it immediately.

### Method 2: Google Drive (batch)
Tony uploads photos to the **Receipts** folder on Google Drive. The nightly cron job (11 PM ET) processes them, or Tony can say "process my receipts" anytime.

## How It Works

1. Receipt image is analyzed by Gemini vision API
2. Extracted data: vendor, date, subtotal, GST/HST, PST, total, category, payment method
3. Data is appended to an Excel spreadsheet with CRA-compliant categories
4. Receipt image is backed up to Google Drive `Receipts/Processed/`
5. Category summary sheet is auto-updated

## Run Receipt Processor

```bash
python3 /home/tonygale/openclaw/skills/receipts/scripts/process_receipts.py
```

Options:
- `--dry-run` — Show what would be processed without making changes
- `--reprocess` — Reprocess all receipts (including previously processed ones)

## Check Expense Summary

```bash
python3 /home/tonygale/openclaw/skills/receipts/scripts/summary.py
```

Options:
- `--month YYYY-MM` — Filter by month (default: current month)
- `--year YYYY` — Full year summary
- `--category "Category Name"` — Filter by CRA category

## CRA Expense Categories

The following categories are used (per CRA T2125 and general business expense guidelines):

- **Advertising & Marketing** — ads, social media, promotional materials
- **Meals & Entertainment** — client meals, business entertainment (50% deductible)
- **Office Supplies** — stationery, printer ink, small equipment
- **Professional Fees** — legal, accounting, consulting
- **Rent & Lease** — office rent, equipment leases
- **Telephone & Internet** — phone bills, internet, data plans
- **Travel** — flights, hotels, car rentals, mileage
- **Vehicle Expenses** — fuel, maintenance, insurance, parking
- **Software & Subscriptions** — SaaS, cloud services, licenses
- **Equipment & Assets** — computers, tools, furniture (CCA eligible)
- **Insurance** — business insurance premiums
- **Training & Education** — courses, certifications, books
- **Bank & Interest Charges** — service fees, interest on business loans
- **Shipping & Delivery** — postage, courier, freight
- **Subcontractors** — payments to contractors
- **Other** — uncategorized expenses

## File Locations

- **Receipts folder (Drive):** `gdrive:Receipts/`
- **Processed folder (Drive):** `gdrive:Receipts/Processed/`
- **Excel file (Drive):** `gdrive:Accountant/Expenses_2026.xlsx`
- **Local working dir:** `/data/.openclaw/workspace/receipts/`
- **Local Excel copy:** `/data/.openclaw/workspace/receipts/Expenses_2026.xlsx`

## Processing a Telegram Photo (Step by Step)

When Tony sends a photo in Telegram:

1. **Save the image** to `/data/.openclaw/workspace/receipts/incoming/`
2. **Run analysis (no --save):**
   ```bash
   python3 /home/tonygale/openclaw/skills/receipts/scripts/process_single.py /data/.openclaw/workspace/receipts/incoming/filename.jpg
   ```
3. **Read the JSON output** and respond to Tony based on the `status` field:
   - `"not_a_receipt"` → Tell Tony what the image appears to be
   - `"unreadable"` → Tell Tony the image is too blurry/damaged, share specific issues, ask for a retake
   - `"poor_quality"` → Show partial data, list what's unclear, ask Tony to confirm or retake
   - `"success"` → Show the extracted data (vendor, date, total, tax, category) and ask Tony to confirm
4. **If Tony confirms**, re-run with `--save`:
   ```bash
   python3 /home/tonygale/openclaw/skills/receipts/scripts/process_single.py /data/.openclaw/workspace/receipts/incoming/filename.jpg --save
   ```
5. **Report:** "Saved! [vendor] — $[total] on [date] under [category]"

## Quality Feedback Examples

**Good quality:**
> Receipt from Tim Hortons — $12.47 on 2026-03-07. Category: Meals & Entertainment. GST: $0.56. Shall I save this?

**Poor quality:**
> I can partially read this receipt. I see it's from Home Depot, but the total and date are blurry. I got: ~$147.xx on March 2026. Can you retake the photo? Make sure the bottom of the receipt (with the total) is in focus.

**Unreadable:**
> This receipt photo is too blurry to read. I can't make out the vendor, date, or total. Tips: hold your phone steady, make sure there's good lighting, and capture the entire receipt. Please retake and send again.

**Not a receipt:**
> This doesn't look like a receipt — it appears to be a business card. Did you mean to send a receipt?

## Add Expense Manually

When Tony tells Yuri about an expense conversationally (e.g. "I spent $25 on Uber today"), use this script to add it to the spreadsheet:

```bash
python3 /home/tonygale/openclaw/skills/receipts/scripts/add_expense.py --vendor "Uber" --total 25.50 --date "2026-03-07" --category "Travel" --description "Uber ride" --payment "Visa"
```

**Two-step process (same as photo receipts):**
1. Run WITHOUT `--confirmed` to show preview
2. After Tony confirms, run WITH `--confirmed` to save

Options:
- `--vendor` — Vendor name (required)
- `--total` — Total amount (required)
- `--date` — Date YYYY-MM-DD (default: today)
- `--category` — CRA category (default: Other)
- `--description` — Description of purchase
- `--subtotal` — Subtotal before tax (auto-calculated if not set)
- `--gst` — GST/HST amount (default: 0)
- `--pst` — PST amount (default: 0)
- `--currency` — Currency code (default: CAD)
- `--payment` — Payment method
- `--source` — Source description (default: "Manual entry")
- `--confirmed` — Actually save (ONLY after Tony confirms)

## Scan Email Receipts

Scan Gmail for receipt emails (Uber, Amazon, etc.) and extract expense data:

```bash
python3 /home/tonygale/openclaw/skills/receipts/scripts/scan_email_receipts.py --list
```

Options:
- `--days N` — How many days back to scan (default: 7)
- `--from "uber"` — Filter by sender
- `--list` — Preview found receipts without saving
- `--save-all` — Save all found receipts to spreadsheet
- `--reprocess` — Ignore tracker and re-scan all emails (use sparingly)

**Always use `--list` first** to show Tony what was found, then `--save-all` after confirmation.

### Dedup Tracker

The scanner tracks processed emails in `/data/.openclaw/workspace/receipts/processed_emails.json`. This means:
- Emails are only sent to Gemini **once** — whether they're receipts or not
- Re-running the scan is fast and free (no API tokens wasted on already-seen emails)
- Use `--reprocess` to force re-scanning if needed (e.g. if Gemini made an error)

## Phone Setup (Alternative to Telegram)

Tony can also upload receipt photos directly to the **Receipts** folder on Google Drive. The nightly cron job at 11 PM ET processes them automatically.
