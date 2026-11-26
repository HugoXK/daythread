import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daythread import connectors, crypto
from daythread.db import connect, init_db
from daythread.errors import ValidationError


NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


class CalendarOpener:
    def __call__(self, request):
        return json.dumps(
            {
                "items": [
                    {
                        "id": "evt-1",
                        "summary": "Design review",
                        "start": {"dateTime": "2026-11-26T15:00:00Z"},
                        "end": {"dateTime": "2026-11-26T16:00:00Z"},
                    }
                ]
            }
        ).encode("utf-8")


class GmailOpener:
    def __call__(self, request):
        if "messages?" in request.full_url:
            return json.dumps({"messages": [{"id": "msg-1"}]}).encode("utf-8")
        return json.dumps(
            {
                "id": "msg-1",
                "snippet": "Can we move the call?",
                "internalDate": "1795000000000",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "investor@example.com"},
                        {"name": "Subject", "value": "Follow-up"},
                    ]
                },
            }
        ).encode("utf-8")


class GoogleConnectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_calendar_adapter(self):
        items = connectors._sync_google_calendar(
            NOW, {"access_token": "x"}, opener=CalendarOpener()
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["payload"]["title"], "Design review")
        self.assertEqual(items[0]["external_id"], "evt-1")

    def test_gmail_adapter(self):
        items = connectors._sync_google_gmail(
            NOW, {"access_token": "x"}, opener=GmailOpener()
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["payload"]["sender"], "investor@example.com")
        self.assertEqual(items[0]["payload"]["subject"], "Follow-up")

    def test_oauth_connect_requires_authorization(self):
        with self.assertRaises(ValidationError):
            connectors.connect(self.conn, "google.calendar")

    def test_sync_requires_token(self):
        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at) VALUES('google.calendar', '1.0.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        with self.assertRaises(ValidationError):
            connectors.sync(self.conn, "google.calendar", now=NOW, home=self.home)

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_sync_with_stored_token(self):
        from daythread import oauth

        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at) VALUES('google.calendar', '1.0.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        oauth.TokenStore(self.conn, self.home).save(
            "google.calendar",
            {"access_token": "x", "refresh_token": "r", "expires_at": "2099-01-01T00:00:00+00:00"},
        )
        result = connectors.sync(
            self.conn,
            "google.calendar",
            now=NOW,
            home=self.home,
            opener=CalendarOpener(),
        )
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
