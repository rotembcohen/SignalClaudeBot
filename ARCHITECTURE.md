# Architecture

## Previous Design (v1 — polling)

The bot spawned a new `signal-cli` process for every operation:
- `signal-cli receive` every 2 seconds (poll loop) — 3s JVM startup each time
- `signal-cli send` per reply — another 3s JVM startup
- Total latency per message: ~10-17 seconds

This doesn't scale to multiple bots (N bots = N JVM startups per cycle).

## Current Design (v2 — JSON-RPC daemon)

### Core Change

Each bot runs `signal-cli jsonRpc` as a **single long-lived subprocess**. The JVM starts once and stays warm. Communication happens via stdin/stdout using JSON-RPC 2.0.

- **Receiving**: Messages are pushed instantly as JSON-RPC notifications (no polling)
- **Sending**: Write a JSON-RPC request to stdin, response comes back on stdout
- **Result**: Near-zero latency for receive/send (no JVM startup, no polling delay)

### Multi-Bot Support

Each bot is defined in `bots.json`:

```json
[
  {
    "name": "Claude",
    "account": "+13477810184",
    "group_id": "X9waBgOUtZIk0So6/fmkKO6lXItj4MrT/7xf/pKyrN0=",
    "device_id": 2
  }
]
```

- Each entry gets its own `signal-cli jsonRpc` subprocess
- All bots run concurrently in a single `asyncio` event loop
- Adding a bot = adding an entry to `bots.json` + registering the Signal number

### Message Flow

```
Phone sends message to group
        ↓
signal-cli jsonRpc pushes notification to stdout
        ↓
asyncio reader parses JSON line
        ↓
Filter: is it a group message for us? (dataMessage or syncMessage.sentMessage)
        ↓
Command dispatch: /sessions, /resume, /new, or pass to claude
        ↓
ask_claude(): subprocess call to `claude -p` (still subprocess — claude CLI has no daemon mode)
        ↓
Send reply: write JSON-RPC "send" request to signal-cli stdin
        ↓
Reply appears in group
```

### Key Design Decisions

1. **asyncio, not threads** — signal-cli jsonRpc is naturally async (readline from stdout). asyncio is the right fit. `claude -p` is still blocking subprocess but runs via `asyncio.create_subprocess_exec`.

2. **One process, multiple bots** — simpler to manage than N separate processes. Single `python3 bot.py` to start/stop everything.

3. **Config file, not constants** — `bots.json` replaces hardcoded `ACCOUNT`/`GROUP_ID`. Adding a bot doesn't require code changes.

4. **Linked device sync messages** — Still handled. The filter checks both `dataMessage` (from other group members) and `syncMessage.sentMessage` (from the primary phone on the same account). Uses `device_id` from config to avoid echo loops.

5. **Session state is per-bot** — Each bot has its own `active_session_id` and `last_session_list`.

### Files

- `bot.py` — all bot logic (single file, stdlib only)
- `bots.json` — bot configurations

### Manual Setup Required (per new bot)

1. **Get a phone number** that can receive SMS for Signal registration
2. **Register with signal-cli**:
   ```bash
   signal-cli -a +1NEWPHONENUMBER register
   signal-cli -a +1NEWPHONENUMBER verify CODE
   ```
   Or link as a secondary device:
   ```bash
   signal-cli link -n "BotName"
   ```
3. **Add the number to the Signal group** from your phone
4. **Add an entry to `bots.json`** with the account, group_id, and device_id
5. **Restart the bot**: `python3 bot.py`
