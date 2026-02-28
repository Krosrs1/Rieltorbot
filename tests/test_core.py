import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from userbot import LeadAnalyzer, LeadStorage, load_config, resolve_notification_target, validate_runtime_config


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path("config.json"))
        validate_runtime_config(self.config)

    def test_analyzer_detects_valid_buy_lead(self):
        analyzer = LeadAnalyzer(self.config)
        text = "Куплю квартиру срочно, бюджет 12 000 000 рублей, район Приморский, звоните +79991234567"
        decision = analyzer.analyze(text)
        self.assertTrue(decision.is_lead)
        self.assertEqual(decision.category, "buy")

    def test_analyzer_rejects_irrelevant_message(self):
        analyzer = LeadAnalyzer(self.config)
        decision = analyzer.analyze("Добрый день, обсуждаем новости рынка")
        self.assertFalse(decision.is_lead)


    def test_runtime_config_accepts_bot_username_target(self):
        config = {"notification": {"target_bot_username": "@my_leads_bot"}, "keywords": {}, "rules": {}}
        validate_runtime_config(config)
        self.assertEqual(resolve_notification_target(config), "@my_leads_bot")

    def test_storage_deduplicates_by_chat_and_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = LeadStorage(Path(tmp) / "leads.db")
            now = datetime.now(timezone.utc)
            inserted1 = storage.insert_message(
                message_id=1,
                chat_id=100,
                user_id=200,
                chat_title="Test",
                message_text="продам квартиру, бюджет 8 000 000 рублей",
                category="sell",
                status="lead",
                reasons=["sell_keywords:1", "has_budget"],
                created_at=now,
            )
            inserted2 = storage.insert_message(
                message_id=1,
                chat_id=100,
                user_id=200,
                chat_title="Test",
                message_text="продам квартиру, бюджет 8 000 000 рублей",
                category="sell",
                status="lead",
                reasons=["sell_keywords:1", "has_budget"],
                created_at=now,
            )
            storage.close()

            self.assertTrue(inserted1)
            self.assertFalse(inserted2)


if __name__ == "__main__":
    unittest.main()
