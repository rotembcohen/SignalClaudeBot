# Debugging: Bot Not Responding to Signal Messages

## Problem
The bot starts without errors, logs show it's polling, but it never responds to messages sent in the Signal group.

## What We Tried
1. **Linked device sync** — Added `signal-cli sendSyncRequest` on startup to re-sync the linked device with the primary phone. No change.
2. **Verified config** — `ACCOUNT` and `GROUP_ID` constants match the actual Signal account and group.
3. **Bot runs clean** — No crashes, no exceptions in the log. The main loop is cycling normally.
4. **Added debug prints** — Traced every stage: poll raw output, envelope parsing, filter decisions, send results.
5. **Ran manual `signal-cli receive`** — Confirmed signal-cli returns envelopes (sync messages), so the connection works.
6. **Sent test message and captured envelope** — Revealed the real envelope format (see Root Cause).

## Initial Hypotheses

### H1: Envelopes don't match our filters ← CONFIRMED (ROOT CAUSE)
### H2: `signal-cli receive` returns nothing ← Partially true (first call timed out, but subsequent calls worked)
### H3: Messages are receipt/typing envelopes, not data messages ← CONFIRMED (they were syncMessage, not dataMessage)
### H4: Group ID mismatch or encoding issue ← Ruled out (group ID matches perfectly in sync envelopes)
### H5: `signal-cli receive` consumed by another process ← Ruled out
### H6: Messages filtered as self ← CONFIRMED (source == ACCOUNT for all sync messages)

## Root Cause

**The bot is a linked device (Device 2) on the same Signal account as the user's phone (Device 1).** When the user sends a message from their phone, it arrives on Device 2 as a `syncMessage.sentMessage` — NOT as a `dataMessage`.

Actual envelope structure received:
```json
{
  "envelope": {
    "source": "+13477810184",
    "sourceDevice": 1,
    "syncMessage": {
      "sentMessage": {
        "message": "Trying now",
        "groupInfo": {
          "groupId": "X9waBgOUtZIk0So6/fmkKO6lXItj4MrT/7xf/pKyrN0="
        }
      }
    }
  }
}
```

The original code failed at TWO filter checks:
1. `source == ACCOUNT` → skipped as "own message" (but it's from Device 1, not Device 2)
2. `env.get("dataMessage")` → returned None (message is in `syncMessage.sentMessage`, not `dataMessage`)

## Fix Applied

Updated `filter_group_messages()` to handle both envelope types:
- **`dataMessage`**: Messages from other group members (source != ACCOUNT) — original path
- **`syncMessage.sentMessage`**: Messages from the primary phone (sourceDevice == 1) synced to this linked device — new path

Added `OWN_DEVICE_ID = 2` constant to skip sync messages originating from our own device (preventing echo loops when the bot sends replies).

## Debug Prints Added
Still present in `bot.py` for continued testing:

- **`poll_messages()`**: Logs raw stdout length and first 500 chars
- **`filter_group_messages()`**: Logs each envelope's source, sourceDevice, keys, and accept/skip reason
- **`send_response()`**: Logs command, return code, stdout, stderr
