# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Signal Claude Bot is a single-file Python bot (`bot.py`) that bridges a Signal group chat with the Claude Code CLI. It polls for incoming Signal group messages via `signal-cli`, forwards them to `claude -p`, and sends Claude's response back to the group.

## Running the Bot

```bash
python3 bot.py
```

Stop with `Ctrl+C` (handles SIGINT/SIGTERM gracefully).

## Dependencies

- **signal-cli**: Must be installed and on PATH, configured for the account in `ACCOUNT`.
- **claude CLI** (Claude Code): Must be installed and on PATH.
- **Python 3.8+**: No third-party Python packages required (stdlib only).

## Architecture

The bot runs a single-threaded poll loop in `main()`:

1. **`poll_messages()`** — Shells out to `signal-cli receive` with JSON output, parses newline-delimited JSON envelopes.
2. **`filter_group_messages()`** — Filters for data messages in the target group, ignoring the bot's own messages.
3. **`ask_claude()`** — Shells out to `claude -p <prompt>`, strips `CLAUDECODE` env var to avoid nested session errors. Truncates responses exceeding `MAX_RESPONSE_LEN`.
4. **`send_response()`** — Shells out to `signal-cli send` to post the reply to the group.

All external process calls use `subprocess.run` with timeouts. Configuration constants are at the top of `bot.py` (`ACCOUNT`, `GROUP_ID`, `POLL_TIMEOUT`, `CLAUDE_TIMEOUT`, `MAX_RESPONSE_LEN`, `SLEEP_BETWEEN_POLLS`).
