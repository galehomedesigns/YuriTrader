import imaplib
import email
import os
from email.header import decode_header

GMAIL_USER = os.environ.get("GMAIL_USER", "decadesdevelopments@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

def decode_mime_header(header_value):
    if not header_value:
        return ""
    decoded_parts = decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)

def search_no_subject_tony_recent():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    # Search for ALL emails from Tony
    _, msg_ids = mail.search(None, '(FROM "tonygale11@gmail.com")')
    if not msg_ids[0]:
        print("No emails found from tonygale11@gmail.com")
        mail.logout()
        return

    ids = msg_ids[0].split()
    print(f"Found {len(ids)} emails from Tony. Searching for NO SUBJECT...")
    
    found_count = 0
    for msg_id in ids[::-1]: # Check from newest
        _, msg_data = mail.fetch(msg_id, "(RFC822)")
        if not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        
        subject = decode_mime_header(msg.get("Subject", ""))
        date = msg.get("Date", "")
        
        if not subject.strip():
            found_count += 1
            print(f"ID: {msg_id.decode()} | Date: {date} | Subject: (empty)")
            # Peek at body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors='replace')
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors='replace')
            print(f"Body snippet: {body[:300]}...")
            print("-" * 40)
        
        if found_count >= 10: break # stop after 10

    mail.logout()

if __name__ == "__main__":
    search_no_subject_tony_recent()
