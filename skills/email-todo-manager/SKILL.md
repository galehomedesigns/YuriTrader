---
name: email-todo-manager
description: Automatically extract and manage a to-do list from your emails. Use when Tony asks to "process my emails into a to-do list," "what are my pending tasks," or "update my task list from my inbox." This skill identifies action items, sets priorities, and estimates due dates based on email content.
---

# Email To-Do Manager

This skill helps Tony stay on top of his business by transforming unread or recent emails into an organized to-do list.

## Workflows

### 1. Generate To-Do List
Run the generator script to pull recent emails and extract potential tasks.
```bash
python3 /home/tonygale/openclaw/skills/email-todo-manager/scripts/generate_todo.py
```

### 2. Review and Refine
After running the script, read the generated file: `/data/.openclaw/workspace/email_todos.xlsx`.
Identify:
- **Priorities**: (High/Medium/Low)
- **Due Dates**: Based on meeting invites or specific deadline mentions.
- **Context**: Map tasks to specific projects (e.g., The Project Wheel, WETech).

### 3. Standards & Categorization
Tasks should be prioritized based on:
- **High**: Investor-related, immediate deadlines (next 48h), or revenue-blocking.
- **Medium**: Project development, standard meetings, general follow-ups.
- **Low**: Long-term research, non-urgent surveys.

## References
- See [CRA_STANDARDS.md](references/CRA_STANDARDS.md) for how to tag expenses related to these tasks.
- Refer to USER.md (`/data/.openclaw/workspace/USER.md`) for mapping tasks to business goals and projects.
