import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daythread import connectors, crypto, oauth, runtime
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


class ActionOpener:
    def __init__(self, payload=None):
        self.payload = payload or {"id": "created-1"}
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return json.dumps(self.payload).encode("utf-8")


class MicrosoftCalendarOpener:
    def __call__(self, request):
        return json.dumps(
            {
                "value": [
                    {
                        "id": "ms-evt-1",
                        "subject": "Board meeting",
                        "start": {"dateTime": "2026-11-26T09:00:00Z"},
                        "end": {"dateTime": "2026-11-26T10:00:00Z"},
                    }
                ]
            }
        ).encode("utf-8")


class MicrosoftMailOpener:
    def __call__(self, request):
        return json.dumps(
            {
                "value": [
                    {
                        "id": "ms-msg-1",
                        "subject": "Quarterly update",
                        "bodyPreview": "Numbers attached.",
                        "receivedDateTime": "2026-11-26T08:00:00Z",
                        "from": {"emailAddress": {"address": "cfo@example.com"}},
                    }
                ]
            }
        ).encode("utf-8")


class PaginatedCalendarOpener:
    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if "pageToken" in request.full_url:
            return json.dumps(
                {"items": [{"id": "evt-2", "summary": "Second", "start": {"dateTime": "x"}}]}
            ).encode("utf-8")
        return json.dumps(
            {
                "items": [{"id": "evt-1", "summary": "First", "start": {"dateTime": "x"}}],
                "nextPageToken": "page-2",
            }
        ).encode("utf-8")


class PaginatedMicrosoftOpener:
    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if request.full_url.endswith("next"):
            return json.dumps(
                {"value": [{"id": "ms-2", "subject": "Second", "start": {"dateTime": "x"}}]}
            ).encode("utf-8")
        return json.dumps(
            {
                "value": [{"id": "ms-1", "subject": "First", "start": {"dateTime": "x"}}],
                "@odata.nextLink": "https://graph.example/next",
            }
        ).encode("utf-8")


class RateLimitThenOkOpener:
    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if self.calls == 1:
            import urllib.error

            raise urllib.error.HTTPError(
                request.full_url, 429, "rate limited", {"Retry-After": "0"}, None
            )
        return json.dumps(
            {"items": [{"id": "evt-1", "summary": "After retry", "start": {"dateTime": "x"}}]}
        ).encode("utf-8")


class SyncTokenOpener:
    def __init__(self):
        self.urls = []

    def __call__(self, request):
        self.urls.append(request.full_url)
        if "syncToken=" in request.full_url:
            item = {"id": "evt-2", "summary": "Second", "start": {"dateTime": "x"}}
            return json.dumps({"items": [item], "nextSyncToken": "tok2"}).encode("utf-8")
        item = {"id": "evt-1", "summary": "First", "start": {"dateTime": "x"}}
        return json.dumps({"items": [item], "nextSyncToken": "tok1"}).encode("utf-8")


class MicrosoftDeltaOpener:
    def __init__(self):
        self.urls = []

    def __call__(self, request):
        self.urls.append(request.full_url)
        if request.full_url.endswith("delta1"):
            return json.dumps(
                {
                    "value": [{"id": "ms-2", "subject": "Second", "start": {"dateTime": "x"}}],
                    "@odata.deltaLink": "https://graph.example/delta2",
                }
            ).encode("utf-8")
        return json.dumps(
            {
                "value": [{"id": "ms-1", "subject": "First", "start": {"dateTime": "x"}}],
                "@odata.deltaLink": "https://graph.example/delta1",
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

    def test_google_calendar_create_action(self):
        opener = ActionOpener({"id": "new-event"})
        result = connectors._action_google_calendar_create(
            None,
            None,
            {"access_token": "x"},
            {"title": "Review", "start": "2026-11-26T10:00:00Z", "end": "2026-11-26T11:00:00Z"},
            opener,
        )
        self.assertEqual(result["id"], "new-event")
        self.assertEqual(opener.requests[0].get_method(), "POST")

    def test_gmail_draft_action(self):
        opener = ActionOpener({"id": "draft-1"})
        result = connectors._action_google_gmail_draft(
            None,
            None,
            {"access_token": "x"},
            {"to": "maya@example.com", "subject": "Deck", "body": "Hi"},
            opener,
        )
        self.assertEqual(result["id"], "draft-1")
        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertIn("raw", body["message"])

    def test_google_calendar_sync_token(self):
        state = {}
        opener = SyncTokenOpener()
        first = connectors._sync_google_calendar(
            NOW, {"access_token": "x"}, opener=opener, state=state
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(state["sync_token"], "tok1")
        second = connectors._sync_google_calendar(
            NOW, {"access_token": "x"}, opener=opener, state=state
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(state["sync_token"], "tok2")
        self.assertIn("syncToken=tok1", opener.urls[1])

    def test_microsoft_delta_state(self):
        state = {}
        opener = MicrosoftDeltaOpener()
        first = connectors._sync_microsoft_calendar(
            NOW, {"access_token": "x"}, opener=opener, state=state
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(state["calendar_delta"], "https://graph.example/delta1")
        second = connectors._sync_microsoft_calendar(
            NOW, {"access_token": "x"}, opener=opener, state=state
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(state["calendar_delta"], "https://graph.example/delta2")
        self.assertEqual(opener.urls[1], "https://graph.example/delta1")

    def test_state_helpers(self):
        connectors.save_state(self.conn, "google.calendar", {"sync_token": "a"})
        self.assertEqual(connectors.get_state(self.conn, "google.calendar"), {"sync_token": "a"})
        connectors.save_state(self.conn, "google.calendar", {})
        self.assertEqual(connectors.get_state(self.conn, "google.calendar"), {})
        connectors.save_state(self.conn, "google.calendar", {"x": "1"})
        connectors.clear_state(self.conn, "google.calendar")
        self.assertEqual(connectors.get_state(self.conn, "google.calendar"), {})

    def test_google_calendar_pagination(self):
        opener = PaginatedCalendarOpener()
        events = connectors._sync_google_calendar(
            NOW, {"access_token": "x"}, opener=opener
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(opener.calls, 2)

    def test_microsoft_pagination(self):
        opener = PaginatedMicrosoftOpener()
        events = connectors._sync_microsoft_calendar(
            NOW, {"access_token": "x"}, opener=opener
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(opener.calls, 2)

    def test_retry_on_429(self):
        original = connectors.HTTP_RETRY_BASE
        connectors.HTTP_RETRY_BASE = 0
        try:
            opener = RateLimitThenOkOpener()
            events = connectors._sync_google_calendar(
                NOW, {"access_token": "x"}, opener=opener
            )
            self.assertEqual(len(events), 1)
            self.assertEqual(opener.calls, 2)
        finally:
            connectors.HTTP_RETRY_BASE = original

    def test_microsoft_read_adapters(self):
        events = connectors._sync_microsoft_calendar(
            NOW, {"access_token": "x"}, opener=MicrosoftCalendarOpener()
        )
        self.assertEqual(events[0]["payload"]["title"], "Board meeting")
        messages = connectors._sync_microsoft_mail(
            NOW, {"access_token": "x"}, opener=MicrosoftMailOpener()
        )
        self.assertEqual(messages[0]["payload"]["sender"], "cfo@example.com")

    def test_microsoft_create_and_send_actions(self):
        opener = ActionOpener({"id": "ms-created"})
        created = connectors._action_microsoft_calendar_create(
            None,
            None,
            {"access_token": "x"},
            {"title": "Review", "start": "2026-11-26T10:00:00Z"},
            opener,
        )
        self.assertEqual(created["id"], "ms-created")
        sent = connectors._action_microsoft_mail_send(
            None,
            None,
            {"access_token": "x"},
            {"to": "maya@example.com", "subject": "Deck", "body": "Hi"},
            opener,
        )
        self.assertEqual(sent["id"], "ms-created")

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_call_action_and_runtime_tool(self):
        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at) VALUES('google.calendar', '1.1.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        oauth.TokenStore(self.conn, self.home).save(
            "google.calendar",
            {"access_token": "x", "refresh_token": "r", "expires_at": "2099-01-01T00:00:00+00:00"},
        )
        result = connectors.call_action(
            self.conn,
            self.home,
            "google.calendar",
            "create_event",
            {"title": "Review", "start": "2026-11-26T10:00:00Z"},
            opener=ActionOpener(),
        )
        self.assertEqual(result["result"]["id"], "created-1")

        connectors.connect(self.conn, "mock.gcal")
        run = runtime.propose(
            self.conn,
            self.home,
            "connector.call",
            {
                "connector": "mock.gcal",
                "action": "write_note",
                "arguments": {"content": "runtime-action"},
            },
        )
        self.assertEqual(run["state"], "pending_approval")
        run = runtime.approve(self.conn, self.home, run["id"])
        self.assertEqual(run["state"], "completed")
        note = self.home / "notes" / "mock-action.md"
        self.assertEqual(note.read_text(encoding="utf-8"), "runtime-action")

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_sync_persists_sync_token(self):
        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at) VALUES('google.calendar', '1.1.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        oauth.TokenStore(self.conn, self.home).save(
            "google.calendar",
            {
                "access_token": "x",
                "refresh_token": "r",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "scope": "https://www.googleapis.com/auth/calendar",
            },
        )
        opener = SyncTokenOpener()
        connectors.sync(
            self.conn, "google.calendar", now=NOW, home=self.home, opener=opener
        )
        self.assertEqual(
            connectors.get_state(self.conn, "google.calendar")["sync_token"], "tok1"
        )
        connectors.sync(
            self.conn, "google.calendar", now=NOW, home=self.home, opener=opener
        )
        self.assertEqual(
            connectors.get_state(self.conn, "google.calendar")["sync_token"], "tok2"
        )
        self.assertIn("syncToken=tok1", opener.urls[1])

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_scope_mismatch_requires_reauthorization(self):
        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at) VALUES('google.calendar', '1.1.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        oauth.TokenStore(self.conn, self.home).save(
            "google.calendar",
            {
                "access_token": "x",
                "refresh_token": "r",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "scope": "https://www.googleapis.com/auth/calendar.readonly",
            },
        )
        with self.assertRaises(ValidationError):
            connectors.sync(self.conn, "google.calendar", now=NOW, home=self.home)

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_revoke_deletes_token(self):
        self.conn.execute(
            "INSERT INTO connectors(id, version, status, scopes, connected_at) VALUES('google.gmail', '1.1.0', 'connected', '[]', '2026-01-01')"
        )
        self.conn.commit()
        oauth.TokenStore(self.conn, self.home).save(
            "google.gmail", {"access_token": "x", "refresh_token": "r"}
        )
        connectors.revoke(self.conn, self.home, "google.gmail")
        self.assertIsNone(oauth.TokenStore(self.conn, self.home).load("google.gmail"))
        row = self.conn.execute(
            "SELECT status FROM connectors WHERE id = 'google.gmail'"
        ).fetchone()
        self.assertEqual(row["status"], "disconnected")

    def test_scope_helper(self):
        self.assertTrue(
            connectors.oauth_scopes_ok(
                ["https://www.googleapis.com/auth/calendar"],
                ["calendar", "openid"],
            )
        )
        self.assertFalse(
            connectors.oauth_scopes_ok(
                ["https://www.googleapis.com/auth/calendar"],
                ["https://www.googleapis.com/auth/gmail.readonly"],
            )
        )

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
