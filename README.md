# Signal Claude Bot

A bot that listens for messages in a Signal group and responds using Claude Code CLI.

## Prerequisites

- `signal-cli` installed and configured for account `+13477810184`
- `claude` CLI installed (Claude Code)
- Python 3.8+

## Usage

```bash
python3 ~/Code/SignalClaudeBot/bot.py
```

Send a message to the "Claude messages" Signal group and the bot will reply with Claude's response.

## Configuration

Edit the constants at the top of `bot.py`:

| Variable | Description |
|---|---|
| `ACCOUNT` | Signal account phone number |
| `GROUP_ID` | Base64 group ID for the target group |
| `POLL_TIMEOUT` | Seconds to wait during signal-cli receive |
| `CLAUDE_TIMEOUT` | Max seconds for claude -p to respond |
| `MAX_RESPONSE_LEN` | Truncate responses beyond this length |

Stop the bot with `Ctrl+C`.

## Linked Device Sync

Since `signal-cli` runs as a linked device (not the primary), it can silently stop receiving messages if it falls out of sync with the primary phone. To prevent this, the bot automatically calls `signal-cli sendSyncRequest` on startup, which asks the primary device to re-sync pending data. If you notice the bot is running but not receiving messages, restarting it should fix the issue.
