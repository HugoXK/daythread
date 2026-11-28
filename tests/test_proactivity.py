import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daythread import memory, proactivity
from daythread.db import connect, init_db

NOON = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)


class ProactivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_signal_dedupe(self):
        first = proactivity.ingest_signal(
            self.conn, "calendar", "conflict", {"title": "Overlap"}, dedupe_key="k", now=NOON
        )
        second = proactivity.ingest_signal(
            self.conn, "calendar", "conflict", {"title": "Overlap"}, dedupe_key="k", now=NOON
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(proactivity.list_signals(self.conn)), 1)

    def test_signal_dedupe_is_scoped_by_source(self):
        first = proactivity.ingest_signal(
            self.conn, "calendar", "event", {"title": "Overlap"}, dedupe_key="k", now=NOON
        )
        second = proactivity.ingest_signal(
            self.conn,
            "tasks",
            "event",
            {"title": "Overlap"},
            dedupe_key="k",
            now=NOON,
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["taint"], "trusted")
        self.assertEqual(second["taint"], "trusted")
        self.assertEqual(len(proactivity.list_signals(self.conn)), 2)

    def test_untrusted_signal_is_not_deduped_against_trusted(self):
        trusted = proactivity.ingest_signal(
            self.conn, "calendar", "event", {"title": "Overlap"}, dedupe_key="k", now=NOON
        )
        untrusted = proactivity.ingest_signal(
            self.conn,
            "calendar",
            "event",
            {"title": "Overlap"},
            dedupe_key="k",
            taint="untrusted",
            now=NOON,
        )
        self.assertNotEqual(trusted["id"], untrusted["id"])
        self.assertEqual(trusted["taint"], "trusted")
        self.assertEqual(untrusted["taint"], "untrusted")
        self.assertEqual(len(proactivity.list_signals(self.conn)), 2)

    def test_quiet_hours(self):
        self.assertTrue(proactivity.can_interrupt(self.conn, NOON))
        self.assertFalse(proactivity.can_interrupt(self.conn, NIGHT))

    def test_budget(self):
        for _ in range(3):
            proactivity.record_interrupt(self.conn, NOON)
        self.assertFalse(proactivity.can_interrupt(self.conn, NOON))
        budget = proactivity.ensure_budget(self.conn, NOON)
        self.assertEqual(budget["sent"], 3)

    def test_candidates_and_feedback(self):
        self.conn.execute(
            "INSERT INTO tasks(id, title, status, due, created_at, updated_at) VALUES('t1', 'Ship it', 'open', ?, ?, ?)",
            (NOON.date().isoformat(), NOON.isoformat(), NOON.isoformat()),
        )
        self.conn.commit()
        memory.add_fact(self.conn, "self", "self", "commitment", "send deck")
        proactivity.ingest_signal(
            self.conn, "demo", "note", {"title": "Review launch"}, dedupe_key="d", now=NOON
        )
        result = proactivity.proactive(self.conn, now=NOON, deliver=True)
        self.assertGreaterEqual(len(result["selected"]), 1)
        self.assertGreater(result["budget"]["sent"], 0)
        candidate = result["selected"][0]
        proactivity.feedback(self.conn, candidate["id"], "accept")
        accepted = proactivity.list_candidates(self.conn, status="accepted")
        self.assertEqual(accepted[0]["id"], candidate["id"])

    def test_delivery_deferred_at_night(self):
        self.conn.execute(
            "INSERT INTO tasks(id, title, status, due, created_at, updated_at) VALUES('t1', 'Ship it', 'open', ?, ?, ?)",
            (NIGHT.date().isoformat(), NIGHT.isoformat(), NIGHT.isoformat()),
        )
        self.conn.commit()
        result = proactivity.proactive(self.conn, now=NIGHT, deliver=True)
        self.assertTrue(result["deferred"])
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["budget"]["sent"], 0)


if __name__ == "__main__":
    unittest.main()
