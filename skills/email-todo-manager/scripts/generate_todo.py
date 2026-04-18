import os
import json
import pandas as pd
from datetime import datetime, timedelta
import subprocess
import re

def get_emails():
    # Reuse the existing Gmail skill to read emails
    # This assumes the Gmail skill is configured
    try:
        # We'll use the read_email.py script from the gmail skill directly
        result = subprocess.run(['python3', '/data/skills/gmail/scripts/read_email.py', '--count', '30'], capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            print(f"Error reading emails: {result.stderr}")
            return []
    except Exception as e:
        print(f"Exception reading emails: {e}")
        return []

def extract_todos(emails):
    todos = []
    # This is a basic heuristic. A real AI would do this better, 
    # but for a deterministic script, we'll look for keywords.
    # The Agent usually calls this script and then parses the result.
    
    action_keywords = ['please', 'need', 'review', 'discuss', 'meeting', 'reminder', 'action', 'task', 'todo']
    
    for email in emails:
        body = email.get('body', '').lower()
        subject = email.get('subject', '').lower()
        
        # Simple priority and date extraction logic
        priority = "Medium"
        if "urgent" in body or "asap" in body or "priority" in body:
            priority = "High"
        elif "low" in body:
            priority = "Low"
            
        # Try to find dates (very basic)
        due_date = "TBD"
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', body)
        if date_match:
            due_date = date_match.group(1)
        elif "monday" in body:
            due_date = "Upcoming Monday"
        elif "friday" in body:
            due_date = "Upcoming Friday"

        if any(k in body or k in subject for k in action_keywords):
            todos.append({
                'Source': 'Gmail',
                'From': email.get('from'),
                'Subject': email.get('subject'),
                'Priority': priority,
                'Due Date': due_date,
                'Status': 'New'
            })
    return todos

def save_to_todo_list(todos):
    todo_file = '/data/.openclaw/workspace/email_todos.xlsx'
    df = pd.DataFrame(todos)
    
    if os.path.exists(todo_file):
        existing_df = pd.read_excel(todo_file)
        # Merge and avoid duplicates based on Subject (simple logic)
        df = pd.concat([existing_df, df]).drop_duplicates(subset=['Subject'], keep='first')
    
    df.to_excel(todo_file, index=False)
    return todo_file

if __name__ == "__main__":
    emails = get_emails()
    if emails:
        todos = extract_todos(emails)
        if todos:
            file_path = save_to_todo_list(todos)
            print(f"Generated {len(todos)} items in {file_path}")
        else:
            print("No new to-do items found.")
    else:
        print("No emails found to process.")
