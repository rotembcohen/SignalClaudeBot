#!/usr/bin/env python3
"""Signal Claude Bot — listens for group messages and responds via Claude Code CLI."""

import glob
import json
import logging
import os
import signal
import subprocess
import sys
import time

# Configuration
ACCOUNT = "+13477810184"
GROUP_ID = "X9waBgOUtZIk0So6/fmkKO6lXItj4MrT/7xf/pKyrN0="
POLL_TIMEOUT = 30  # seconds to wait for signal-cli receive
CLAUDE_TIMEOUT = 120  # seconds to wait for claude -p
MAX_RESPONSE_LEN = 6000  # Signal message size limit
SLEEP_BETWEEN_POLLS = 2  # seconds between poll cycles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

shutdown_requested = False
active_session_id = None
last_session_list = []


def handle_signal(signum, _frame):
    global shutdown_requested
    log.info("Received signal %s, shutting down...", signum)
    shutdown_requested = True


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def list_sessions(limit=10):
    """Scan all sessions-index.json files and return the most recent sessions."""
    claude_projects = os.path.expanduser("~/.claude/projects")
    index_files = glob.glob(os.path.join(claude_projects, "*/sessions-index.json"))

    all_sessions = []
    for path in index_files:
        try:
            with open(path) as f:
                data = json.load(f)
            for entry in data.get("entries", []):
                # Derive project name from projectPath
                project_path = entry.get("projectPath", "")
                entry["projectName"] = os.path.basename(project_path) if project_path else "unknown"
                all_sessions.append(entry)
        except (json.JSONDecodeError, OSError) as e:
            log.debug("Failed to read %s: %s", path, e)

    all_sessions.sort(key=lambda s: s.get("modified", ""), reverse=True)
    return all_sessions[:limit]


def format_session_list(sessions):
    """Format sessions as a numbered list for display."""
    if not sessions:
        return "No sessions found."
    lines = []
    for i, s in enumerate(sessions, 1):
        summary = s.get("summary") or s.get("firstPrompt", "")[:60] or "(no summary)"
        project = s.get("projectName", "unknown")
        modified = s.get("modified", "")[:10]  # YYYY-MM-DD
        count = s.get("messageCount", 0)
        lines.append(f"{i}. {summary}\n   Project: {project} | Modified: {modified} | Messages: {count}")
    return "\n".join(lines)


def poll_messages():
    """Run signal-cli receive and return parsed JSON envelope list."""
    try:
        result = subprocess.run(
            ["signal-cli", "-a", ACCOUNT, "-o", "json", "receive", "-t", str(POLL_TIMEOUT)],
            capture_output=True,
            text=True,
            timeout=POLL_TIMEOUT + 10,
        )
    except subprocess.TimeoutExpired:
        log.warning("signal-cli receive timed out")
        return []
    except FileNotFoundError:
        log.error("signal-cli not found — is it installed and on PATH?")
        return []

    if result.returncode != 0:
        log.warning("signal-cli receive exited %d: %s", result.returncode, result.stderr.strip())

    messages = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            log.debug("Skipping non-JSON line: %s", line[:120])
    return messages


def filter_group_messages(messages):
    """Keep only data messages from the target group, ignoring our own."""
    filtered = []
    for envelope in messages:
        env = envelope.get("envelope", envelope)
        source = env.get("source") or env.get("sourceNumber", "")
        if source == ACCOUNT:
            continue  # ignore own messages

        data = env.get("dataMessage")
        if not data:
            continue

        group_info = data.get("groupInfo", {})
        if group_info.get("groupId") != GROUP_ID:
            continue

        body = data.get("message") or data.get("body") or ""
        if not body.strip():
            continue

        filtered.append({"source": source, "text": body.strip()})
    return filtered


def ask_claude(prompt, session_id=None):
    """Send prompt to claude -p and return the response text."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # avoid nested session errors

    cmd = ["claude", "-p", prompt]
    if session_id:
        cmd.extend(["--resume", session_id])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "Sorry, I took too long to respond. Please try again."
    except FileNotFoundError:
        log.error("claude CLI not found — is it installed and on PATH?")
        return "Error: Claude CLI not available."

    if result.returncode != 0:
        log.warning("claude -p exited %d: %s", result.returncode, result.stderr.strip()[:200])
        return "Sorry, something went wrong while generating a response."

    response = result.stdout.strip()
    if len(response) > MAX_RESPONSE_LEN:
        response = response[: MAX_RESPONSE_LEN - 20] + "\n\n[...truncated]"
    return response or "(empty response)"


def send_response(text):
    """Send a message to the Signal group."""
    try:
        result = subprocess.run(
            ["signal-cli", "-a", ACCOUNT, "send", "-g", GROUP_ID, "-m", text],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning("Failed to send message: %s", result.stderr.strip()[:200])
    except subprocess.TimeoutExpired:
        log.warning("signal-cli send timed out")
    except FileNotFoundError:
        log.error("signal-cli not found — is it installed and on PATH?")


def sync_linked_device():
    """Request sync from primary device to ensure this linked device is up to date."""
    log.info("Requesting sync from primary device...")
    try:
        subprocess.run(
            ["signal-cli", "-a", ACCOUNT, "sendSyncRequest"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        log.info("Sync request sent.")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("Failed to send sync request: %s", e)


def main():
    global active_session_id, last_session_list
    log.info("Signal Claude Bot starting (account=%s)", ACCOUNT)
    sync_linked_device()
    log.info("Listening for messages in group %s", GROUP_ID)

    while not shutdown_requested:
        try:
            messages = poll_messages()
            incoming = filter_group_messages(messages)

            for msg in incoming:
                text = msg["text"]
                log.info("Message from %s: %s", msg["source"], text[:80])

                if text.strip().lower() == "/sessions":
                    sessions = list_sessions()
                    last_session_list = sessions
                    response = format_session_list(sessions)
                elif text.strip().lower().startswith("/resume"):
                    parts = text.strip().split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip().isdigit():
                        response = "Usage: /resume <number> (use /sessions to see the list)"
                    else:
                        idx = int(parts[1].strip())
                        if not last_session_list:
                            response = "No session list available. Run /sessions first."
                        elif idx < 1 or idx > len(last_session_list):
                            response = f"Invalid number. Pick 1-{len(last_session_list)}."
                        else:
                            session = last_session_list[idx - 1]
                            sid = session.get("sessionId")
                            if not sid:
                                response = "That session has no ID. Pick another."
                            else:
                                active_session_id = sid
                                summary = session.get("summary") or session.get("firstPrompt", "")[:60]
                                response = f"Resumed session: {summary}\nAll messages will now continue this conversation. Send /new to reset."
                elif text.strip().lower() == "/new":
                    active_session_id = None
                    response = "Session cleared. Back to stateless mode."
                else:
                    response = ask_claude(text, session_id=active_session_id)

                log.info("Response (%d chars): %s", len(response), response[:80])
                send_response(response)

        except Exception:
            log.exception("Unexpected error in main loop")

        if not shutdown_requested:
            time.sleep(SLEEP_BETWEEN_POLLS)

    log.info("Bot stopped.")


if __name__ == "__main__":
    main()
