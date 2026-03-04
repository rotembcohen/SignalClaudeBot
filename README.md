# Signal Claude Bot

A bot that listens for messages in a Signal group and responds using Claude Code CLI.

## Prerequisites

- `signal-cli` installed and configured for account `+13477810184`
- `claude` CLI installed (Claude Code)
- Python 3.8+

## Usage

```bash
python3 ~/Code/SignalClaudeBot/bot.py
python3 ~/Code/SignalClaudeBot/bot.py --yolo  # skip all Claude permission checks
```

Send a message to the "Claude messages" Signal group and the bot will reply with Claude's response.

The `--yolo` flag passes `--dangerously-skip-permissions` to Claude, allowing it to run any tool (Bash, file writes, etc.) without approval. Without it, permissions are controlled by `.claude/settings.json`.

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

## Commands

The bot supports these commands sent as messages in the Signal group:

| Command | Description |
|---|---|
| `/sessions` | Lists the 10 most recent Claude conversations across all projects. Each entry shows a number, summary, project name, last modified date, and message count. |
| `/resume <number>` | Resumes a conversation from the last `/sessions` list. All subsequent messages will continue that conversation with full history. |
| `/new` | Clears the active session, returning to stateless mode (each message is independent). |

### Example workflow

1. Send `/sessions` to see recent conversations
2. Send `/resume 3` to pick conversation #3
3. Send messages as usual — they continue that conversation's context
4. Send `/new` when done to go back to stateless mode

## Architecture

The bot is a single-file Python app (`bot.py`) with a single-threaded poll loop:

1. **`poll_messages()`** — Shells out to `signal-cli receive` with JSON output, parses newline-delimited JSON envelopes.
2. **`filter_group_messages()`** — Filters for data messages in the target group, ignoring the bot's own messages.
3. **Command dispatch** — Checks if the message is `/sessions`, `/resume`, or `/new` and handles it directly.
4. **`ask_claude()`** — Shells out to `claude -p <prompt>` (with optional `--resume <sessionId>` for session mode). Strips the `CLAUDECODE` env var to avoid nested session errors. Truncates responses exceeding `MAX_RESPONSE_LEN`.
5. **`send_response()`** — Shells out to `signal-cli send` to post the reply to the group.

### Session management

Session state is read from Claude's own data files at `~/.claude/projects/*/sessions-index.json`. Each file contains an `entries` array with objects like:

```json
{
  "sessionId": "uuid",
  "summary": "Description of the conversation",
  "messageCount": 20,
  "modified": "2026-01-20T17:44:16.024Z",
  "projectPath": "/Users/.../MyProject"
}
```

The `list_sessions()` function scans all these index files, merges and sorts entries by modified date, and returns the top 10. The selected session ID is stored in a global `active_session_id` and passed to `claude -p --resume <id>` for subsequent messages.

## Linked Device Sync

Since `signal-cli` runs as a linked device (not the primary), it can silently stop receiving messages if it falls out of sync with the primary phone. To prevent this, the bot automatically calls `signal-cli sendSyncRequest` on startup, which asks the primary device to re-sync pending data. If you notice the bot is running but not receiving messages, restarting it should fix the issue.
