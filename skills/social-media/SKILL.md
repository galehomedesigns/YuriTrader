---
name: social-media
description: Draft and manage social media content for Decades Developments and The Project Wheel. Use when Tony asks to create posts, plan content, or manage the content calendar.
---

# Social Media Content Management

Draft, review, and track social media posts for Decades Developments.

## Content Workflow (MANDATORY)

1. **Research** — Use gmail, youtube, web_search to gather source material
2. **Draft** — Write the post and save to `/data/.openclaw/workspace/content-library/drafts/`
3. **Present** — Show the draft to Tony on Telegram and ask for approval
4. **Post** — Only after Tony approves. Move the file to `posted/` after publishing.

⚠️ **NEVER post to any platform without Tony's explicit approval.**

## Draft File Format

Save drafts as markdown files with YAML frontmatter:

```markdown
---
platform: linkedin
status: draft
date: 2026-03-07
source: youtube video / email / original
---

# Post Title (internal reference)

[Post content here]

---
Hashtags: #ProjectManagement #Construction #AI
```

## Platform Guidelines

### LinkedIn
- Professional tone, industry-focused
- 1300 chars max for full visibility (before "see more")
- Use line breaks for readability
- End with a question or call-to-action
- Hashtags: 3-5 relevant ones at the bottom
- Tag relevant industry groups/people when appropriate

### Facebook
- Slightly more casual than LinkedIn
- Can be longer-form
- Include a call-to-action (visit website, comment, share)
- Use emojis sparingly for visual breaks

### General
- Always tie content back to The Project Wheel or industry expertise
- Reference real industry challenges (cost overruns, schedule delays, fragmented tools)
- Be confident and authoritative — Decades Developments knows this space
- Avoid generic AI hype — focus on practical, real-world application

## Content Library

- Drafts: `/data/.openclaw/workspace/content-library/drafts/`
- Posted: `/data/.openclaw/workspace/content-library/posted/`
- Quotes & talking points: `/data/.openclaw/workspace/content-library/`

## Research Sources

- **Gmail** — Check decadesdevelopments@gmail.com for newsletters, client communications
- **YouTube** — Search for Tony's videos or industry content to repurpose
- **Web search** — Industry news, competitor analysis, trending topics
- **Podcasts** — Tony's podcast episodes (ask Tony for links/RSS feed)
