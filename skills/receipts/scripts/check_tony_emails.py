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

def check_sent_absolute():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    
    mail.select('"[Gmail]/Sent Mail"', readonly=True)
    _, msg_ids = mail.search(None, 'ALL')
    ids = msg_ids[0].split()
    print(f"Total in Sent Mail: {len(ids)}")
    
    for msg_id in ids[-5:]:
        _, msg_data = mail.fetch(msg_id, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = decode_mime_header(msg.get("Subject", ""))
        to_addr = decode_mime_header(msg.get("To", ""))
        date = msg.get("Date", "")
        print(f"  ID: {msg_id.decode()} | To: {to_addr} | Subject: {subject} | Date: {date}")

    mail.logout()

if __name__ == "__main__":
    check_sent_absolute()

if __name__ == "__main__":
    search_all_folders()
