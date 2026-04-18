import os
import smtplib
import imaplib
import email
from email.header import decode_header

def search_v5():
    user = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    
    print(f"Connecting to {user}...")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(user, password)
        mail.select("inbox")
        
        # Search for 'v5' in the subject or body
        # IMAP search for v5
        status, messages = mail.search(None, '(OR SUBJECT "v5" BODY "v5")')
        
        if status != 'OK':
            print("No messages found.")
            return

        msg_ids = messages[0].split()
        print(f"Found {len(msg_ids)} possible matches.")
        
        for msg_id in msg_ids[::-1]: # Check latest first
            res, msg_data = mail.fetch(msg_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8")
                    
                    print(f"Subject: {subject}")
                    
                    for part in msg.walk():
                        if part.get_content_maintype() == 'multipart':
                            continue
                        if part.get('Content-Disposition') is None:
                            continue
                        
                        filename = part.get_filename()
                        if filename:
                            if 'v5' in filename.lower() or 'v5' in subject.lower():
                                print(f"FOUND ATTACHMENT: {filename}")
                                filepath = os.path.join("/data/.openclaw/workspace/downloads", filename)
                                with open(filepath, "wb") as f:
                                    f.write(part.get_payload(decode=True))
                                print(f"Saved to {filepath}")
                                mail.logout()
                                return
        mail.logout()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    search_v5()
