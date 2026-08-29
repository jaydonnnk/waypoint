"""Telegram update handlers (S3 — /start + photo ingest + confirm/redo +
typed-entry fallback).

Photo handler: download → extract_passport → mrz.validate →
  ok: show masked confirm card (inline keyboard Confirm/Redo) →
  on confirm: STORE.add_traveler + deleteMessage on the photo.
  fail: typed-entry fallback → same mrz.validate gate.

The session state machine (session.py) tracks which phase each chat is in.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.bot.session import SessionStore
from app.config import int_env
from app.db.store import DeskStore
from app.events import DeskEvent, EventSink

logger = logging.getLogger(__name__)

# S4 guard 4: reject OVERSIZED photos BEFORE extraction. 10 MB — generous
# for a passport photo, but stops multi-hundred-MB blobs from reaching the
# VL model (memory + cost). ENV-tunable for testing. (Malformed / non-image
# blobs are not rejected here — they fail closed via extract_passport's
# except into the typed-entry fallback, not an explicit rejection.)
# Tolerant read shared with the API routes (app.config, M-new2): a malformed
# OR below-minimum override falls back to the default rather than crashing
# bot import (config-typo DoS) or disabling the size gate.
MAX_PHOTO_BYTES = int_env("WAYBOT_MAX_PHOTO_BYTES", 10 * 1024 * 1024, minimum=1)

# Module-level session store (per-chat conversation state).
SESSIONS = SessionStore()


# ---------------------------------------------------------------------------
# /start deep-link handler (S2, unchanged)
# ---------------------------------------------------------------------------

async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start deep-link handler: parse `?start=<token>`, resolve via
    DeskStore.bind_chat, upsert chat_bindings, reply with confirmation
    or an error.
    """
    chat_id = str(update.effective_chat.id)

    if not context.args:
        await update.message.reply_text(
            "Welcome to Waybot! Use the share link from your manager "
            "to get started."
        )
        return

    token = context.args[0]
    store: DeskStore = context.bot_data["store"]

    try:
        result = await asyncio.to_thread(store.bind_chat, chat_id, token)
    except Exception:  # noqa: BLE001 — traveler must always get a reply
        logger.exception("bind_chat failed for chat %s (isolated)", chat_id)
        await update.message.reply_text(
            "⚠️ Something went wrong. Please try again in a moment."
        )
        return

    if result is None:
        await update.message.reply_text(
            "⚠️ That link isn't valid, has expired, or the team is full. "
            "Ask your manager for a fresh share link."
        )
        return

    desk_id, slot = result
    SESSIONS.bind(chat_id, desk_id, slot)

    await update.message.reply_text(
        f"✅ You're linked to desk {desk_id[:8]}… as traveler #{slot}.\n"
        "Send a photo of your passport when you're ready."
    )
    logger.info("chat %s bound to desk %s slot %d", chat_id, desk_id, slot)


# ---------------------------------------------------------------------------
# Photo ingest handler (S3)
# ---------------------------------------------------------------------------

async def _on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Photo handler: download → extract → validate → confirm card or fallback."""
    chat_id = str(update.effective_chat.id)
    session = SESSIONS.get(chat_id)

    if session is None or session.phase == "done":
        await update.message.reply_text(
            "Use the share link from your manager first (/start)."
        )
        return

    if session.phase not in ("awaiting_photo", "awaiting_confirm"):
        # Already in typed-entry flow — remind them.
        await update.message.reply_text(
            "Please type your passport details as requested above, "
            "or send /start with a new link to restart."
        )
        return

    store: DeskStore = context.bot_data["store"]
    sink: EventSink = context.bot_data["sink"]

    # If a prior photo is still un-cleaned (resend before confirm), delete
    # it now so an earlier passport image never lingers in the chat.
    if session.photo_message_id is not None:
        await _try_delete(context, chat_id, session.photo_message_id)

    # Stash the new photo message_id for later deleteMessage.
    session.photo_message_id = update.message.message_id

    # Pick the largest photo variant.
    photo = update.message.photo[-1]  # largest resolution

    # S4 guard 4 (L1): reject oversized photos BEFORE spending the download.
    # Telegram reports file_size on the PhotoSize, so a huge blob is refused
    # without ever pulling the bytes into memory. The post-download check
    # below stays authoritative (file_size can be absent or under-report).
    if photo.file_size is not None and photo.file_size > MAX_PHOTO_BYTES:
        await update.message.reply_text(
            f"⚠️ Photo too large ({photo.file_size // 1024}KB). "
            "Please send a smaller photo of your passport."
        )
        return

    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    # Authoritative size gate: reject oversized photos BEFORE extraction. The
    # VL model should never see a multi-hundred-MB blob — memory + cost + abuse.
    if len(image_bytes) > MAX_PHOTO_BYTES:
        await update.message.reply_text(
            f"⚠️ Photo too large ({len(image_bytes) // 1024}KB). "
            "Please send a smaller photo of your passport."
        )
        return

    # Extract MRZ via Qwen-VL (or injected transport).
    try:
        from app.bot.extract import extract_passport

        transport = context.bot_data.get("extract_transport")
        fields_raw = await extract_passport(bytes(image_bytes), transport=transport)
    except Exception:  # noqa: BLE001 — fallback to typed entry
        logger.exception("extract_passport failed for chat %s (isolated)", chat_id)
        session.phase = "awaiting_typed"
        await update.message.reply_text(
            "⚠️ Couldn't read your passport. Please type your details:\n\n"
            "Format: FAMILY_NAME / GIVEN_NAME / GENDER(M/F) / "
            "BIRTHDAY(YYYY-MM-DD) / NATIONALITY(2-letter) / "
            "DOC_NUMBER / ISSUING_COUNTRY(2-letter) / EXPIRY(YYYY-MM-DD)"
        )
        return

    # Validate via MRZ check digits.
    from app.bot.mrz import MrzFields, validate

    validated = validate(fields_raw)
    if validated is None:
        session.phase = "awaiting_typed"
        await update.message.reply_text(
            "⚠️ Passport check digits didn't pass. Please type your details:\n\n"
            "Format: FAMILY_NAME / GIVEN_NAME / GENDER(M/F) / "
            "BIRTHDAY(YYYY-MM-DD) / NATIONALITY(2-letter) / "
            "DOC_NUMBER / ISSUING_COUNTRY(2-letter) / EXPIRY(YYYY-MM-DD)\n\n"
            "All fields are validated — no free text."
        )
        # Fire provenance event: fallback used.
        sink.publish(DeskEvent(
            type="fallback_used",
            desk_id=session.desk_id,
            payload={"slot": session.slot, "reason": "mrz_check_failed"},
        ))
        return

    # Show masked confirm card.
    await _show_confirm_card(update, context, session, validated)


async def _show_confirm_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session,
    fields: "MrzFields",
) -> None:
    """Send the masked confirm card with Confirm/Redo inline keyboard."""
    # Mask doc_number: show first 2 + last 2, mask middle.
    doc = fields.doc_number
    if len(doc) > 4:
        masked_doc = doc[:2] + "*" * (len(doc) - 4) + doc[-2:]
    else:
        masked_doc = doc

    # No parse_mode: MRZ-derived names may carry stray Markdown control
    # chars (*, _, [) from OCR that would break rendering or crash the
    # send after the session already moved to awaiting_confirm. Plain text
    # is the safe sink for untrusted extracted strings.
    text = (
        f"📋 Passport Details\n\n"
        f"Name: {fields.family_name} / {fields.given_name}\n"
        f"Gender: {fields.gender}\n"
        f"Birthday: {fields.birthday}\n"
        f"Nationality: {fields.nationality_iso2}\n"
        f"Document: {masked_doc}\n"
        f"Expiry: {fields.doc_expiry}\n\n"
        "Is this correct?"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm_passport"),
            InlineKeyboardButton("🔄 Redo", callback_data="redo_passport"),
        ]
    ])

    # Stash validated fields on the session for the confirm callback.
    session._pending_fields = fields
    session.phase = "awaiting_confirm"

    msg = await update.message.reply_text(text, reply_markup=keyboard)
    session.confirm_message_id = msg.message_id


# ---------------------------------------------------------------------------
# Inline keyboard callbacks (Confirm / Redo)
# ---------------------------------------------------------------------------

async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Confirm/Redo inline keyboard presses."""
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat_id)
    session = SESSIONS.get(chat_id)

    if session is None:
        await query.edit_message_text("Session expired. Use /start to begin again.")
        return

    if query.data == "confirm_passport":
        await _confirm_passport(query, context, session, chat_id)
    elif query.data == "redo_passport":
        await _redo_passport(query, context, session, chat_id)


async def _confirm_passport(query, context, session, chat_id: str) -> None:
    """Store the traveler and clean up."""
    fields = getattr(session, "_pending_fields", None)
    if fields is None:
        await query.edit_message_text("No pending passport data. Send a new photo.")
        session.phase = "awaiting_photo"
        return

    store: DeskStore = context.bot_data["store"]
    sink: EventSink = context.bot_data["sink"]

    try:
        await asyncio.to_thread(
            store.add_traveler,
            session.desk_id,
            session.slot,
            fields,
        )
    except ValueError:
        # A duplicate doc number — do NOT echo the raw doc number/desk_id
        # into the reply (PII / project unmasked-doc standard).
        await query.edit_message_text(
            "⚠️ That document is already registered on this trip. "
            "Please send a different passport."
        )
        session.phase = "awaiting_photo"
        return
    except Exception:  # noqa: BLE001 — always reply
        logger.exception("add_traveler failed for chat %s (isolated)", chat_id)
        await query.edit_message_text("⚠️ Something went wrong. Please try again.")
        session.phase = "awaiting_photo"
        return

    # Delete the original photo message (PII minimization).
    if session.photo_message_id is not None:
        await _try_delete(context, chat_id, session.photo_message_id)
        session.photo_message_id = None

    session.phase = "done"
    session._pending_fields = None

    await query.edit_message_text(
        "✅ Passport verified and stored. You're all set!\n"
        "Your manager will be notified when everyone's ready."
    )

    # Backend-side travelers_complete: check if N/N verified.
    from app.travelers import maybe_fire_travelers_complete
    await maybe_fire_travelers_complete(store, sink, session.desk_id)


async def _redo_passport(query, context, session, chat_id: str) -> None:
    """Let the traveler re-send their photo. Delete the stashed photo now —
    the traveler rejected it, so the image should not linger in the chat."""
    if session.photo_message_id is not None:
        await _try_delete(context, chat_id, session.photo_message_id)
        session.photo_message_id = None
    session.phase = "awaiting_photo"
    session._pending_fields = None
    await query.edit_message_text(
        "🔄 No problem — send a new photo of your passport."
    )


async def _try_delete(context, chat_id: str, message_id: int) -> None:
    """Best-effort Telegram deleteMessage (PII minimization). Never raises."""
    try:
        await context.bot.delete_message(
            chat_id=int(chat_id), message_id=message_id
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.warning("deleteMessage failed in chat %s (isolated)", chat_id)


# ---------------------------------------------------------------------------
# Typed-entry fallback handler (S3)
# ---------------------------------------------------------------------------

async def _on_typed_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle typed passport details when MRZ extraction failed."""
    chat_id = str(update.effective_chat.id)
    session = SESSIONS.get(chat_id)

    if session is None or session.phase != "awaiting_typed":
        return  # Not in typed-entry flow — ignore plain text.

    store: DeskStore = context.bot_data["store"]
    sink: EventSink = context.bot_data["sink"]
    text = update.message.text.strip()

    # Parse: FAMILY_NAME / GIVEN_NAME / GENDER / BIRTHDAY / NATIONALITY / DOC_NUMBER / ISSUING / EXPIRY
    parts = [p.strip() for p in text.split("/")]
    if len(parts) != 8:
        await update.message.reply_text(
            "⚠️ Expected 8 fields separated by /. Format:\n"
            "FAMILY_NAME / GIVEN_NAME / GENDER(M/F) / "
            "BIRTHDAY(YYYY-MM-DD) / NATIONALITY(2-letter) / "
            "DOC_NUMBER / ISSUING_COUNTRY(2-letter) / EXPIRY(YYYY-MM-DD)"
        )
        return

    family, given, gender, birthday, nationality, doc_num, issuing, expiry = parts

    # Route through the SAME gate module as the photo path: curated-CSV
    # nationality (fail-closed), real calendar dates, expiry-not-past.
    from app.bot.mrz import build_typed_fields

    fields = build_typed_fields(
        family_name=family,
        given_name=given,
        gender=gender,
        birthday=birthday,
        nationality=nationality,
        doc_number=doc_num,
        issuing_country=issuing,
        doc_expiry=expiry,
    )
    if fields is None:
        await update.message.reply_text(
            "⚠️ Couldn't validate those details. Check that:\n"
            "• Gender is M or F\n"
            "• Nationality/issuing are 2-letter ISO codes (e.g. SG, US, GB)\n"
            "• Dates are YYYY-MM-DD and the passport isn't expired\n"
            "• Family name and document number are filled in"
        )
        return

    try:
        await asyncio.to_thread(
            store.add_traveler,
            session.desk_id,
            session.slot,
            fields,
        )
    except ValueError:
        # Duplicate doc number — never echo the raw value back.
        await update.message.reply_text(
            "⚠️ That document is already registered on this trip. "
            "Check the document number."
        )
        return
    except Exception:  # noqa: BLE001
        logger.exception("add_traveler failed for chat %s (isolated)", chat_id)
        await update.message.reply_text("⚠️ Something went wrong. Please try again.")
        return

    # Delete the stashed photo message (PII minimization) — the typed-entry
    # path must clean up the original photo just like the Confirm path does.
    if session.photo_message_id is not None:
        await _try_delete(context, chat_id, session.photo_message_id)
        session.photo_message_id = None

    session.phase = "done"

    # Provenance: typed-entry fallback was used.
    sink.publish(DeskEvent(
        type="fallback_used",
        desk_id=session.desk_id,
        payload={"slot": session.slot, "reason": "typed_entry"},
    ))

    await update.message.reply_text(
        "✅ Details verified and stored. You're all set!\n"
        "Your manager will be notified when everyone's ready."
    )

    from app.travelers import maybe_fire_travelers_complete
    await maybe_fire_travelers_complete(store, sink, session.desk_id)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_handlers(application: Application, store: DeskStore) -> None:
    """Wire the bot's command/message/callback handlers."""
    application.bot_data["store"] = store
    application.add_handler(CommandHandler("start", _start))
    application.add_handler(MessageHandler(filters.PHOTO, _on_photo))
    application.add_handler(CallbackQueryHandler(_on_callback))
    # Text handler for typed-entry fallback — lowest priority (only fires
    # when phase == "awaiting_typed").
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _on_typed_entry)
    )
    application.add_error_handler(_error_handler)


async def _error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log and swallow — one bad update must never crash the bot."""
    logger.exception(
        "Unhandled exception in update %s (isolated)",
        update,
        exc_info=context.error,
    )
