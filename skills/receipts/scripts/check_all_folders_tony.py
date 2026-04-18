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

def search_spam_trash_tony():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    
    folders = ['"[Gmail]/Spam"', '"[Gmail]/Trash"', '"[Gmail]/All Mail"']
    
    for folder in folders:
        print(f"Checking folder: {folder}...")
        status, _ = mail.select(folder, readonly=True)
        if status != 'OK':
            print(f"  Could not select {folder}")
            continue
            
        _, msg_ids = mail.search(None, '(FROM "tonygale11@gmail.com")')
        if not msg_ids[0]:
            print(f"  No emails from Tony in {folder}")
            continue
            
        ids = msg_ids[0].split()
        print(f"  Found {len(ids)} emails from Tony in {folder}. Checking latest 5...")
        for msg_id in ids[-5:]:
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = decode_mime_header(msg.get("Subject", ""))
            date = msg.get("Date", "")
            print(f"    ID: {msg_id.decode()} | Date: {date} | Subject: '{subject}'")

    mail.logout()

if __name__ == "__main__":
    search_spam_trash_tony()
