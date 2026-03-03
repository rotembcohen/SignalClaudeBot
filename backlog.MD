# Backlog

## High Priority

001. [ ] **Error recovery / auto-restart** — If signal-cli crashes or disconnects, the bot should automatically restart the subprocess instead of exiting
002. [ ] **Message queue / rate limiting** — Prevent flooding Signal with rapid replies; queue outgoing messages with a small delay between sends
003. [x] **Conversation context in groups** — Currently each group message is sent to Claude statelessly; add opt-in threaded context so Claude remembers recent conversation

## Medium Priority

004. [ ] **Multi-message support** — Handle long Claude responses by splitting them across multiple Signal messages instead of truncating at 6000 chars
005. [ ] **Attachments / image support** — Forward image attachments to Claude (vision) and send back image results if applicable
006. [ ] **Per-user session tracking** — Track active sessions per user instead of per bot, so multiple users can have independent conversations
007. [ ] **Admin commands** — Add `/status`, `/help`, and `/config` commands for runtime bot management
008. [ ] **Update CLAUDE.md** — Architecture section still references old polling design (poll_messages, filter_group_messages, send_response) but the code now uses JSON-RPC

## Low Priority

009. [ ] **Typing indicators** — Send typing indicators while Claude is processing a response
010. [ ] **Message reactions** — React to incoming messages (e.g., thumbs up) to acknowledge receipt before responding
011. [ ] **Logging to file** — Add optional file-based logging with rotation for production use
012. [ ] **Docker / containerization** — Add a Dockerfile for easier deployment
013. [ ] **Health check endpoint** — Expose a simple HTTP endpoint for monitoring bot status
