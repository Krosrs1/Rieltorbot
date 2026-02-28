#!/usr/bin/env python3
"""Telegram Userbot for collecting real-estate leads from groups/channels.

Features:
- Monitors all chats where the user account is present (groups/channels).
- Detects potential buy/sell leads using configurable keywords/rules (JSON).
- Persists all processed messages to SQLite with lead status.
- Sends Telegram notifications only for validated leads.
- Handles duplicates and provides runtime statistics via logging.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

@dataclass(slots=True)
class LeadDecision:
    is_lead: bool
    reasons: list[str]
    category: str


class LeadAnalyzer:
    """Analyzes text against configurable lead rules."""

    PHONE_REGEX = re.compile(r"(?:(?:\+7|7|8)[\s\-()]*)?(?:\d[\s\-()]*){10,11}")
    EMAIL_REGEX = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", re.IGNORECASE)

    def __init__(self, config: dict[str, Any]) -> None:
        keywords = config["keywords"]
        rules = config["rules"]

        self.buy_keywords = self._normalize_list(keywords.get("buy", []))
        self.sell_keywords = self._normalize_list(keywords.get("sell", []))
        self.urgency_keywords = self._normalize_list(keywords.get("urgency_interest", []))
        self.realtor_keywords = self._normalize_list(keywords.get("realtor_help", []))
        self.detail_keywords = self._normalize_list(keywords.get("details", []))

        self.require_explicit_intent = bool(rules.get("require_explicit_intent", True))
        self.min_details_required = int(rules.get("min_details_required", 1))
        self.contact_bonus = bool(rules.get("contact_bonus", True))

        if self.min_details_required < 0:
            self.min_details_required = 0

    @staticmethod
    def _normalize_list(values: list[str]) -> list[str]:
        return [v.strip().lower() for v in values if isinstance(v, str) and v.strip()]

    def analyze(self, text: str) -> LeadDecision:
        cleaned = (text or "").strip().lower()
        if not cleaned:
            return LeadDecision(False, ["empty_message"], "none")

        buy_hits = [kw for kw in self.buy_keywords if kw in cleaned]
        sell_hits = [kw for kw in self.sell_keywords if kw in cleaned]
        urgency_hits = [kw for kw in self.urgency_keywords if kw in cleaned]
        realtor_hits = [kw for kw in self.realtor_keywords if kw in cleaned]
        detail_hits = [kw for kw in self.detail_keywords if kw in cleaned]

        has_budget = bool(re.search(r"\b\d{2,}\s?(?:₽|руб|рублей|р\.)", cleaned)) or any(
            token in cleaned for token in ("бюджет", "₽", "руб", "рублей")
        )
        has_location = any(token in cleaned for token in ("район", "метро", "ул.", "улица", "город", "жк", "этаж"))
        has_area = bool(re.search(r"\b\d{1,4}\s?(?:м²|кв\.м|м2)\b", cleaned)) or any(
            token in cleaned for token in ("площадь", "м²", "кв.м")
        )
        has_contact = bool(self.PHONE_REGEX.search(cleaned) or self.EMAIL_REGEX.search(cleaned))

        details_score = sum([has_budget, has_location, has_area])
        intent_hits = buy_hits + sell_hits

        if buy_hits and not sell_hits:
            category = "buy"
        elif sell_hits and not buy_hits:
            category = "sell"
        elif buy_hits and sell_hits:
            category = "mixed"
        elif realtor_hits:
            category = "realtor_help"
        else:
            category = "none"

        reasons: list[str] = []
        if buy_hits:
            reasons.append(f"buy_keywords:{len(buy_hits)}")
        if sell_hits:
            reasons.append(f"sell_keywords:{len(sell_hits)}")
        if urgency_hits:
            reasons.append(f"urgency_keywords:{len(urgency_hits)}")
        if realtor_hits:
            reasons.append(f"realtor_keywords:{len(realtor_hits)}")
        if detail_hits:
            reasons.append(f"detail_keywords:{len(detail_hits)}")
        if has_budget:
            reasons.append("has_budget")
        if has_location:
            reasons.append("has_location")
        if has_area:
            reasons.append("has_area")
        if has_contact:
            reasons.append("has_contact")

        if self.require_explicit_intent and not intent_hits and not realtor_hits:
            return LeadDecision(False, reasons + ["no_intent"], category)

        if details_score < self.min_details_required and not has_contact:
            return LeadDecision(False, reasons + ["insufficient_details"], category)

        lead_score = 0
        if intent_hits:
            lead_score += 2
        if details_score > 0:
            lead_score += details_score
        if urgency_hits:
            lead_score += 1
        if realtor_hits:
            lead_score += 1
        if self.contact_bonus and has_contact:
            lead_score += 1

        return LeadDecision(lead_score >= 3, reasons, category)


class LeadStorage:
    """SQLite storage layer with duplicate protection."""

    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER,
                chat_title TEXT,
                message_text TEXT NOT NULL,
                message_hash TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                reasons TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, message_id)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_user_id ON leads(user_id)")
        self.conn.commit()

    def insert_message(
        self,
        *,
        message_id: int,
        chat_id: int,
        user_id: int | None,
        chat_title: str,
        message_text: str,
        category: str,
        status: str,
        reasons: list[str],
        created_at: datetime,
    ) -> bool:
        message_hash = sha256(message_text.strip().lower().encode("utf-8")).hexdigest()
        try:
            self.conn.execute(
                """
                INSERT INTO leads (
                    message_id, chat_id, user_id, chat_title, message_text,
                    message_hash, category, status, reasons, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    chat_id,
                    user_id,
                    chat_title,
                    message_text,
                    message_hash,
                    category,
                    status,
                    ",".join(reasons),
                    created_at.isoformat(),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        self.conn.close()


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    required_keys = {"notification", "keywords", "rules"}
    missing = required_keys - set(config.keys())
    if missing:
        raise ValueError(f"Missing config sections: {', '.join(sorted(missing))}")

    return config


def validate_runtime_config(config: dict[str, Any]) -> None:
    """Validate critical runtime config values before bot startup."""
    notification = config.get("notification", {})
    target_id = notification.get("target_telegram_id")
    target_bot_username = notification.get("target_bot_username")

    if target_id is None and not target_bot_username:
        raise ValueError(
            "Set one of: config.notification.target_telegram_id or config.notification.target_bot_username"
        )

    if target_id is not None:
        try:
            int(target_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("config.notification.target_telegram_id must be an integer") from exc

    if target_bot_username:
        if not isinstance(target_bot_username, str) or not target_bot_username.strip().startswith("@"):
            raise ValueError("config.notification.target_bot_username must start with '@'")


def resolve_notification_target(config: dict[str, Any]) -> int | str:
    notification = config["notification"]
    if notification.get("target_bot_username"):
        return str(notification["target_bot_username"]).strip()
    return int(notification["target_telegram_id"])


def prompt_if_empty(value: str | None, label: str) -> str:
    if value:
        return value
    user_value = input(f"Enter {label}: ").strip()
    if not user_value:
        raise ValueError(f"{label} is required")
    return user_value


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_notification(event, decision: LeadDecision) -> str:
    sender = event.sender_id
    chat_name = getattr(event.chat, "title", None) or "Unknown chat"
    created = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    text = (event.raw_text or "")[:700]

    return (
        "🏠 *Новый лид найден*\n"
        f"• Категория: `{decision.category}`\n"
        f"• Чат: {chat_name}\n"
        f"• User ID: `{sender}`\n"
        f"• Время: {created}\n"
        f"• Причины: {', '.join(decision.reasons[:8])}\n\n"
        f"Сообщение:\n```\n{text}\n```"
    )


async def run_bot(args: argparse.Namespace) -> None:
    try:
        from telethon import TelegramClient, events
        from telethon.errors import FloodWaitError, RpcError
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Telethon is not installed. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    config = load_config(Path(args.config))
    validate_runtime_config(config)
    analyzer = LeadAnalyzer(config)
    storage = LeadStorage(Path(args.db))

    api_id = int(prompt_if_empty(args.api_id or os.getenv("API_ID"), "API_ID"))
    api_hash = prompt_if_empty(args.api_hash or os.getenv("API_HASH"), "API_HASH")
    notify_target = resolve_notification_target(config)

    processed_count = 0
    lead_count = 0

    client = TelegramClient(args.session, api_id, api_hash)

    shutdown_started = False

    async def shutdown(*_):
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        logging.info("Shutting down userbot...")
        logging.info("Processed messages: %s | Leads detected: %s", processed_count, lead_count)
        storage.close()
        await client.disconnect()

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):  # noqa: ANN001
        nonlocal processed_count, lead_count
        try:
            if not (event.is_group or event.is_channel):
                return
            if not event.raw_text:
                return

            processed_count += 1
            decision = analyzer.analyze(event.raw_text)
            status = "lead" if decision.is_lead else "not_lead"

            chat_title = getattr(event.chat, "title", None) or str(event.chat_id)
            inserted = storage.insert_message(
                message_id=event.message.id,
                chat_id=event.chat_id,
                user_id=event.sender_id,
                chat_title=chat_title,
                message_text=event.raw_text,
                category=decision.category,
                status=status,
                reasons=decision.reasons,
                created_at=event.message.date.astimezone(timezone.utc),
            )

            if not inserted:
                logging.debug("Duplicate message skipped chat_id=%s message_id=%s", event.chat_id, event.message.id)
                return

            if decision.is_lead:
                lead_count += 1
                logging.info("Lead found in %s (chat_id=%s, user_id=%s)", chat_title, event.chat_id, event.sender_id)
                try:
                    await client.send_message(notify_target, build_notification(event, decision), parse_mode="markdown")
                except FloodWaitError as exc:
                    logging.warning("FloodWait while sending notification: %s seconds", exc.seconds)
                except RpcError as exc:
                    logging.error("Failed to send notification: %s", exc)

            if processed_count % 100 == 0:
                logging.info("Stats: processed=%s, leads=%s", processed_count, lead_count)

        except Exception as exc:  # noqa: BLE001
            logging.exception("Error processing message: %s", exc)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: asyncio_create_task_safe(shutdown()))

    logging.info("Starting userbot session=%s", args.session)
    await client.start()
    logging.info("Userbot started. Monitoring all group/channel messages...")
    await client.run_until_disconnected()


def asyncio_create_task_safe(coro):
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram real-estate lead finder userbot")
    parser.add_argument("--api-id", help="Telegram API_ID (or set API_ID env var)")
    parser.add_argument("--api-hash", help="Telegram API_HASH (or set API_HASH env var)")
    parser.add_argument("--session", default="userbot_session", help="Telethon session name")
    parser.add_argument("--config", default="config.json", help="Path to JSON config")
    parser.add_argument("--db", default="leads.db", help="SQLite database path")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    import asyncio

    args = parse_args()
    setup_logging(args.verbose)

    try:
        asyncio.run(run_bot(args))
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    except Exception as exc:  # noqa: BLE001
        logging.exception("Fatal error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
