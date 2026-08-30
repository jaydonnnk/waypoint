---
kind: logging_system
name: Standard Library `logging` Module — Per-Module Loggers, No Central Configuration
category: logging_system
scope:
    - '**'
source_files:
    - backend/app/main.py
    - backend/app/events.py
    - backend/app/agent/loop.py
    - backend/app/bot/__init__.py
    - backend/app/bot/handlers.py
    - backend/app/approval.py
---

## What system/approach is used

The Waypoint backend uses Python's **standard library `logging` module** exclusively. There is no third-party logging framework (no loguru, structlog, logzero, sentry_sdk, etc.). Each module that needs to emit logs creates a logger via `logger = logging.getLogger(__name__)`, which produces a per-module logger hierarchy rooted at the package name (e.g. `app.main`, `app.events`, `app.agent.loop`, `app.bot.handlers`, `app.approval`).

There is **no central configuration of handlers, formatters, or log levels** anywhere in the codebase: no `logging.basicConfig()` call, no `StreamHandler`/`FileHandler` setup, no `setLevel()` invocation, and no custom formatter injection. The application relies on Python's default root logger behavior (which typically writes `WARNING`+ to stderr) unless an external process supervisor or container runtime configures the root handler before importing the app.

## Key files and packages

- `backend/app/main.py` — FastAPI entry point; defines `logger = logging.getLogger(__name__)` and emits lifecycle logs (`Waybot polling started`, `Waybot supervised task cancelled`, `Waybot build failed — app continues bot-less`).
- `backend/app/events.py` — In-process domain-event sink; each subscriber fault is logged with `logger.exception(...)` so a bad subscriber cannot break the publisher.
- `backend/app/agent/loop.py` — Desk orchestration loop; logs approval-state read failures (`approval state unreadable for desk ...`) and re-approval race conditions.
- `backend/app/bot/__init__.py` — Bot builder; logs when the bot is disabled (`WAYPOINT_BOT_TOKEN not set — bot disabled`, `python-telegram-bot not installed — bot disabled`).
- `backend/app/bot/handlers.py` — Telegram update handlers; logs chat binding events and falls back to loopback API base with a one-time warning.
- `backend/app/approval.py` — Pre-trip approval logic; logs when a `pending_approval` round opens.
- Other modules using the same pattern: `backend/app/travelers.py`, `backend/app/codes.py`, `backend/app/bot/extract.py`, `backend/app/bot/notify.py`.

## Architecture and conventions

1. **Per-module logger instance**: Every file that logs does `import logging` followed by `logger = logging.getLogger(__name__)`. This gives each module its own named logger under the `app.*` namespace, enabling fine-grained filtering if a root handler is configured externally.

2. **Log level usage is consistent with severity**:
   - `logger.info(...)` — normal operational milestones (polling start, chat bound, pending_approval opened).
   - `logger.warning(...)` — recoverable or expected-but-unusual conditions (missing env var fallback, invite probe failure, CAS lost on approval round).
   - `logger.error(...)` — terminal failures that stop a path (unrecoverable token errors, consecutive failure budget exceeded).
   - `logger.exception(...)` — always paired with an `except Exception:` block to capture the traceback while swallowing the exception (symmetric isolation in event delivery, bot cleanup, subscriber dispatch).

3. **Structured-ish messages via positional arguments**: Messages are written as format strings with `%s` placeholders and values passed as separate arguments (e.g. `logger.info("chat %s bound to desk %s slot %d", chat_id, desk_id, slot)`). This lets the underlying handler render them efficiently and keeps PII out of the message template.

4. **No structured fields / JSON envelopes**: Logs are plain text lines. There is no request ID, correlation ID, desk_id field injected into every log line, no JSON serialization, and no enrichment layer. Context like `desk_id` is embedded inline in the message string.

5. **Sinks are separate from logs**: The only cross-cutting output channel beyond stdout/stderr is the in-process `EventSink` (`app.events.SINK`), which publishes typed `DeskEvent`s to subscribers (the Telegram notify handler). Event delivery faults are logged via `logger.exception(...)`, but the events themselves are not log records — they are domain events routed to handlers.

6. **Frontend**: The Next.js frontend (`frontend/app/...`) has no logging framework; it renders server-sent events emitted by the backend loop and does not produce its own logs.

## Conventions and constraints

- **Every module that logs must create its own logger via `logging.getLogger(__name__)`** — this is the observed convention across all modules that import `logging`.
- **Exceptions are caught broadly (`except Exception:`) and logged with `logger.exception(...)` rather than re-raising**, especially around async task scheduling and subscriber delivery. This is documented in comments as "symmetric isolation" — a faulty subscriber or scheduler cannot break the publisher.
- **Sensitive data is never placed in log messages**: comments explicitly state things like "nothing here is printed or logged" (demo passenger builder) and payload reaching the sink is masked upstream before publication.
- **Log-level strategy is implicit, not enforced by code**: there are no programmatic checks against emitting debug logs in production; the convention is that `debug`-level logs are reserved for development-only traces while `info`/`warning`/`error` carry operational meaning.
- **No centralized log rotation, sampling, or destination control exists in code**: because no handlers are attached programmatically, where logs end up depends entirely on how the process is launched (Docker stdout, a process manager, or an external collector). This is a constraint — changing log destinations requires environment/process configuration outside the Python code.