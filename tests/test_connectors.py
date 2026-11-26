import tempfile
import unittest
from pathlib import Path

from daythread import connectors
from daythread.db import connect, init_db


class ConnectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_list_and_connect(self):
        items = connectors.list_connectors(self.conn)
        ids = {item["id"] for item in items}
        self.assertTrue(
            {
                "mock.gcal",
                "mock.gmail",
                "google.calendar",
                "google.gmail",
                "microsoft.calendar",
                "microsoft.mail",
            }
            <= ids
        )
        self.assertTrue(all(item["status"] == "available" for item in items))
        connected = connectors.connect(self.conn, "mock.gmail")
        self.assertEqual(connected["status"], "connected")

    def test_sync_is_idempotent(self):
        connectors.connect(self.conn, "mock.gmail")
        first = connectors.sync(self.conn, "mock.gmail")
        second = connectors.sync(self.conn, "mock.gmail")
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(len(connectors.list_items(self.conn, "mock.gmail")), 2)
        self.assertEqual(len(connectors.list_messages(self.conn)), 2)

    def test_calendar_sync(self):
        connectors.connect(self.conn, "mock.gcal")
        result = connectors.sync(self.conn, "mock.gcal")
        self.assertEqual(result["inserted"], 2)
        rows = self.conn.execute("SELECT * FROM calendar_events").fetchall()
        self.assertEqual(len(rows), 2)

    def test_sync_requires_connection(self):
        with self.assertRaises(ValueError):
            connectors.sync(self.conn, "mock.gmail")

    def test_disconnect(self):
        connectors.connect(self.conn, "mock.gmail")
        connectors.disconnect(self.conn, "mock.gmail")
        with self.assertRaises(ValueError):
            connectors.sync(self.conn, "mock.gmail")

    def test_unknown(self):
        with self.assertRaises(KeyError):
            connectors.connect(self.conn, "nope")


if __name__ == "__main__":
    unittest.main()
