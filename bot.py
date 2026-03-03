#!/usr/bin/env python3
"""Signal Claude Bot — bridges Signal group chats with Claude Code CLI.

Uses signal-cli in JSON-RPC mode (single long-lived subprocess per bot)
for instant message delivery instead of polling. Supports multiple bots
running concurrently via bots.json config.
"""

import asyncio
import glob
import json
import logging
import os
import signal
import sys

# Configuration
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bots.json")
CLAUDE_TIMEOUT = 120  # seconds to wait for claude -p
MAX_RESPONSE_LEN = 6000  # Signal message size limit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def load_config():
    """Load bot configurations from bots.json."""
    with open(CONFIG_FILE) as f:
        bots = json.load(f)
    if not bots:
        log.error("No bots configured in %s", CONFIG_FILE)
        sys.exit(1)
    return bots


# ---------------------------------------------------------------------------
# Session helpers (shared across bots)
# ---------------------------------------------------------------------------

def _current_project_dir():
    """Return the Claude project directory name for the current working directory."""
    cwd = os.getcwd()
    return cwd.replace("/", "-")


def list_sessions(limit=10):
    """Scan .jsonl session files for the current project and return the most recent."""
    claude_projects = os.path.expanduser("~/.claude/projects")
    project_dir = _current_project_dir()
    project_path = os.path.join(claude_projects, project_dir)
    if not os.path.isdir(project_path):
        return []
    session_files = glob.glob(os.path.join(project_path, "*.jsonl"))

    all_sessions = []
    for path in session_files:
        try:
            mtime = os.path.getmtime(path)
            session_id = os.path.splitext(os.path.basename(path))[0]
            # Derive project name from directory name (e.g. -Users-foo-Code-MyProject -> MyProject)
            project_dir = os.path.basename(os.path.dirname(path))
            project_name = project_dir.rsplit("-", 1)[-1] if "-" in project_dir else project_dir

            # Read first user message as summary
            first_prompt = ""
            with open(path) as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "user":
                        msg = obj.get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                for part in content:
                                    if isinstance(part, dict) and part.get("text"):
                                        first_prompt = part["text"]
                                        break
                            elif isinstance(content, str):
                                first_prompt = content
                        elif isinstance(msg, str):
                            first_prompt = msg
                        break

            all_sessions.append({
                "sessionId": session_id,
                "projectName": project_name,
                "modified": mtime,
                "summary": first_prompt[:80] if first_prompt else "(no summary)",
            })
        except OSError as e:
            log.debug("Failed to read %s: %s", path, e)

    all_sessions.sort(key=lambda s: s["modified"], reverse=True)
    return all_sessions[:limit]


def format_session_list(sessions):
    """Format sessions as a numbered list for display."""
    if not sessions:
        return "No sessions found."
    lines = []
    for i, s in enumerate(sessions, 1):
        summary = s.get("summary", "(no summary)")
        project = s.get("projectName", "unknown")
        mtime = s.get("modified", 0)
        from datetime import datetime
        modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "?"
        lines.append(f"{i}. {summary}\n   Project: {project} | Modified: {modified}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude CLI
# ---------------------------------------------------------------------------

def _find_latest_session():
    """Find the most recently modified .jsonl session file and return its session ID."""
    claude_projects = os.path.expanduser("~/.claude/projects")
    session_files = glob.glob(os.path.join(claude_projects, "*/*.jsonl"))
    if not session_files:
        return None
    newest = max(session_files, key=os.path.getmtime)
    return os.path.splitext(os.path.basename(newest))[0]


async def ask_claude(prompt, session_id=None):
    """Send prompt to claude -p and return the response text.

    Returns (response_text, session_id). If no session_id was provided,
    detects the session created by this call so it can be resumed later.
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = ["claude", "-p", prompt]
    if session_id:
        cmd.extend(["--resume", session_id])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLAUDE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return "Sorry, I took too long to respond. Please try again.", session_id
    except FileNotFoundError:
        log.error("claude CLI not found — is it installed and on PATH?")
        return "Error: Claude CLI not available.", session_id

    log.info("claude -p exited %d", proc.returncode)
    log.info("claude -p stdout: %s", stdout.decode().strip()[:500])
    log.info("claude -p stderr: %s", stderr.decode().strip()[:500])

    if proc.returncode != 0:
        err = stderr.decode().strip()[:300]
        msg = f"Error (exit {proc.returncode}): {err}" if err else "Sorry, something went wrong while generating a response."
        return msg, session_id

    # If this was the first message, detect the session ID so we can resume
    if not session_id:
        session_id = _find_latest_session()
        log.info("Auto-detected session: %s", session_id)

    response = stdout.decode().strip()
    if len(response) > MAX_RESPONSE_LEN:
        response = response[:MAX_RESPONSE_LEN - 20] + "\n\n[...truncated]"
    return response or "(empty response)", session_id


# ---------------------------------------------------------------------------
# Signal Bot (one instance per account)
# ---------------------------------------------------------------------------

class SignalBot:
    def __init__(self, config):
        self.name = config["name"]
        self.account = config["account"]
        self.group_id = config["group_id"]
        self.device_id = config.get("device_id", 2)
        self.proc = None
        self._req_id = 0
        self._pending = {}  # id -> Future
        self.active_session_id = None
        self.last_session_list = []

    def _next_id(self):
        self._req_id += 1
        return str(self._req_id)

    async def start(self):
        """Start the signal-cli jsonRpc subprocess."""
        self.proc = await asyncio.create_subprocess_exec(
            "signal-cli", "-a", self.account, "--output=json", "jsonRpc",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info("[%s] signal-cli jsonRpc started (pid=%d)", self.name, self.proc.pid)

        # Start reader and stderr logger
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def stop(self):
        """Stop the signal-cli subprocess."""
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
            log.info("[%s] signal-cli stopped", self.name)

    async def _read_stderr(self):
        """Log stderr output from signal-cli."""
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            text = line.decode().strip()
            if text:
                log.info("[%s] signal-cli stderr: %s", self.name, text)

    async def _read_stdout(self):
        """Read JSON-RPC messages from signal-cli stdout."""
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                log.warning("[%s] signal-cli stdout closed", self.name)
                break

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug("[%s] non-JSON line: %s", self.name, line.decode().strip()[:120])
                continue

            # JSON-RPC response (has "id") — resolve the pending future
            if "id" in msg:
                req_id = str(msg["id"])
                future = self._pending.pop(req_id, None)
                if future and not future.done():
                    future.set_result(msg)
                continue

            # JSON-RPC notification (no "id") — incoming message
            if msg.get("method") == "receive":
                asyncio.create_task(self._handle_envelope(msg.get("params", {})))

    async def _rpc(self, method, params=None):
        """Send a JSON-RPC request and wait for the response."""
        req_id = self._next_id()
        request = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            request["params"] = params

        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        self.proc.stdin.write((json.dumps(request) + "\n").encode())
        await self.proc.stdin.drain()

        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            log.warning("[%s] RPC %s timed out", self.name, method)
            return None

    async def send_message(self, text, recipient=None):
        """Send a message via JSON-RPC. If recipient is set, sends a DM; otherwise sends to the group."""
        if recipient:
            params = {"recipient": [recipient], "message": text}
        else:
            params = {"groupId": self.group_id, "message": text}
        result = await self._rpc("send", params)
        if result and result.get("error"):
            log.warning("[%s] send error: %s", self.name, result["error"])

    async def _handle_envelope(self, params):
        """Process an incoming envelope notification."""
        envelope = params.get("envelope", params)
        source = envelope.get("source") or envelope.get("sourceNumber", "")
        source_device = envelope.get("sourceDevice")

        body = None
        reply_to = None  # None = group reply, phone number = DM reply

        # dataMessage from others
        data = envelope.get("dataMessage")
        if data and source != self.account:
            group_info = data.get("groupInfo", {})
            if group_info.get("groupId") == self.group_id:
                body = data.get("message") or data.get("body") or ""
            elif not group_info.get("groupId"):
                # Direct message (no group)
                body = data.get("message") or data.get("body") or ""
                reply_to = source

        # syncMessage.sentMessage from primary phone (same account)
        if body is None:
            sync = envelope.get("syncMessage", {})
            sent = sync.get("sentMessage")
            if sent and source_device != self.device_id:
                group_info = sent.get("groupInfo", {})
                if group_info.get("groupId") == self.group_id:
                    body = sent.get("message") or sent.get("body") or ""
                elif not group_info.get("groupId"):
                    # Direct message sent from primary phone to this bot
                    dest = sent.get("destination") or sent.get("destinationNumber") or ""
                    if dest == self.account:
                        body = sent.get("message") or sent.get("body") or ""
                        reply_to = source

        if not body or not body.strip():
            return

        text = body.strip()
        where = f"DM from {source}" if reply_to else f"group from {source}"
        log.info("[%s] Message (%s, device %s): %s", self.name, where, source_device, text[:80])
        await self._handle_message(text, reply_to=reply_to)

    async def _handle_message(self, text, reply_to=None):
        """Dispatch commands or forward to Claude."""
        lower = text.strip().lower()

        if lower == "/sessions":
            sessions = list_sessions()
            self.last_session_list = sessions
            response = format_session_list(sessions)

        elif lower.startswith("/resume"):
            parts = text.strip().split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip().isdigit():
                response = "Usage: /resume <number> (use /sessions to see the list)"
            else:
                idx = int(parts[1].strip())
                if not self.last_session_list:
                    response = "No session list available. Run /sessions first."
                elif idx < 1 or idx > len(self.last_session_list):
                    response = f"Invalid number. Pick 1-{len(self.last_session_list)}."
                else:
                    session = self.last_session_list[idx - 1]
                    sid = session.get("sessionId")
                    if not sid:
                        response = "That session has no ID. Pick another."
                    else:
                        self.active_session_id = sid
                        summary = session.get("summary") or session.get("firstPrompt", "")[:60]
                        response = f"Resumed session: {summary}\nAll messages will now continue this conversation. Send /new to reset."

        elif lower == "/new":
            self.active_session_id = None
            response = "Session cleared. Back to stateless mode."

        else:
            response, new_session_id = await ask_claude(text, session_id=self.active_session_id)
            if new_session_id and not self.active_session_id:
                self.active_session_id = new_session_id
                log.info("[%s] Auto-resuming session %s", self.name, new_session_id)

        log.info("[%s] Response (%d chars): %s", self.name, len(response), response[:80])
        await self.send_message(response, recipient=reply_to)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run():
    configs = load_config()
    bots = [SignalBot(cfg) for cfg in configs]

    # Start all bots
    for bot in bots:
        await bot.start()

    log.info("All bots started (%d). Press Ctrl+C to stop.", len(bots))

    # Wait until signal-cli processes exit or we get interrupted
    try:
        await asyncio.gather(*(bot.proc.wait() for bot in bots))
    except asyncio.CancelledError:
        pass
    finally:
        for bot in bots:
            await bot.stop()
        log.info("All bots stopped.")


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Interrupted, shutting down...")


if __name__ == "__main__":
    main()
