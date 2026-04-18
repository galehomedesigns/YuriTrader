---
name: gdrive
description: Access Google Drive for decadesdevelopments@gmail.com. List, download, upload, and search files. Use when Tony asks to check Drive, upload files, or access documents.
---

# Google Drive

Access Google Drive (decadesdevelopments@gmail.com) via rclone.

## Configuration

- Remote name: `gdrive:`
- Config file: `/data/.config/rclone/rclone.conf`
- Always use: `--config /data/.config/rclone/rclone.conf`

## Commands

### List folders
```bash
rclone lsd gdrive: --config /data/.config/rclone/rclone.conf
```

### List files (top level)
```bash
rclone ls gdrive: --config /data/.config/rclone/rclone.conf --max-depth 1
```

### List files in a folder
```bash
rclone ls gdrive:FolderName --config /data/.config/rclone/rclone.conf
```

### Search for files
```bash
rclone ls gdrive: --config /data/.config/rclone/rclone.conf --include "*keyword*"
```

### Download a file
```bash
rclone copy gdrive:path/to/file.pdf /data/.openclaw/workspace/downloads/ --config /data/.config/rclone/rclone.conf
```

### Upload a file
```bash
rclone copy /data/.openclaw/workspace/file.pdf gdrive:FolderName/ --config /data/.config/rclone/rclone.conf
```

### Check storage usage
```bash
rclone about gdrive: --config /data/.config/rclone/rclone.conf
```

## Key Folders

- `Receipts/` — Receipt images (processed ones move to `Receipts/Processed/`)
- `Accountant/` — Expense spreadsheets (Expenses_2026.xlsx)
- `Dashboard/` — Dashboard HTML snapshots
- `Social Media/` — Video storyboard and social content

## Security

- **Confirm with Tony before uploading, overwriting, or deleting files**
- Token auto-refreshes via the refresh_token
- 15 GB free storage
