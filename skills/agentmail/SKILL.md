---
name: agentmail
description: API-first email platform designed for AI agents. Create and manage dedicated email inboxes, send and receive emails programmatically, and handle email-based workflows with webhooks and real-time events. Use when you need to set up agent email identity, send emails from agents, handle incoming email workflows, or replace traditional email providers like Gmail with agent-friendly infrastructure.
---

# AgentMail

AgentMail is an API-first email platform designed specifically for AI agents. Unlike traditional email providers (Gmail, Outlook), AgentMail provides programmatic inboxes, usage-based pricing, high-volume sending, and real-time webhooks.

## Core Capabilities

- **Programmatic Inboxes**: Create and manage email addresses via API
- **Send/Receive**: Full email functionality with rich content support
- **Real-time Events**: Webhook notifications for incoming messages
- **AI-Native Features**: Semantic search, automatic labeling, structured data extraction
- **No Rate Limits**: Built for high-volume agent use

## Quick Start

1. **Create an account** at [console.agentmail.to](https://console.agentmail.to)
2. **Generate API key** in the console dashboard
3. **Install Python SDK**: `pip install agentmail python-dotenv`
4. **Set environment variable**: `AGENTMAIL_API_KEY=your_key_here`

## Basic Operations

### Create an Inbox

```python
from agentmail import AgentMail

client = AgentMail(api_key=os.getenv("AGENTMAIL_API_KEY"))

# Create inbox with custom username
inbox = client.inboxes.create(
    username="spike-assistant",  # Creates spike-assistant@agentmail.to
    client_id="unique-identifier"  # Ensures idempotency
)
print(f"Created: {inbox.inbox_id}")
```

### Send Email

```python
client.inboxes.messages.send(
    inbox_id="spike-assistant@agentmail.to",
    to="adam@example.com",
    subject="Task completed",
    text="The PDF rotation is finished. See attachment.",
    html="<p>The PDF rotation is finished. <strong>See attachment.</strong></p>",
    attachments=[{
        "filename": "rotated.pdf",
        "content": base64.b64encode(file_data).decode()
    }]
)
```

### List Inboxes

```python
inboxes = client.inboxes.list(limit=10)
for inbox in inboxes.inboxes:
    print(f"{inbox.inbox_id} - {inbox.display_name}")
```

## Advanced Features

### Webhooks for Real-Time Processing

Set up webhooks to respond to incoming emails immediately:

```python
# Register webhook endpoint
webhook = client.webhooks.create(
    url="https://your-domain.com/webhook",
    client_id="email-processor"
)
```

See [WEBHOOKS.md](references/WEBHOOKS.md) for complete webhook setup guide including ngrok for local development.

### Custom Domains

For branded email addresses (e.g., `spike@yourdomain.com`), upgrade to a paid plan and configure custom domains in the console.

## Security

Incoming email webhooks are a prompt injection vector. An allowlist filter is configured in OpenClaw hooks.

See [SECURITY.md](references/SECURITY.md) for implementation details and defense layers.

## Scripts Available

- **`scripts/send_email.py`** - Send emails with rich content and attachments
- **`scripts/check_inbox.py`** - Poll inbox for new messages
- **`scripts/setup_webhook.py`** - Configure webhook endpoints for real-time processing

## References

- **[API.md](references/API.md)** - Complete API reference and endpoints
- **[WEBHOOKS.md](references/WEBHOOKS.md)** - Webhook setup and event handling
- **[EXAMPLES.md](references/EXAMPLES.md)** - Common patterns and use cases

## Current Setup

- **Yuri's inbox**: tonygale@agentmail.to
- **API key**: Set as `AGENTMAIL_API_KEY` in environment