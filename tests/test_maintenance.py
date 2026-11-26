import tempfile
import unittest
from pathlib import Path

from daythread import maintenance
from daythread.db import connect, init_db

OLD = "2000-01-01T00:00:00+00:00"
NOW = "2026-11-26T00:00:00+00:00"


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)
        self.conn.execute(
            "INSERT INTO runs(id, intent, tool, args, state, risk, decision, created_at, updated_at) VALUES('old-run','x','note.write','{}','completed','write','allow',?,?)",
            (OLD, OLD),
        )
        self.conn.execute(
            "INSERT INTO runs(id, intent, tool, args, state, risk, decision, created_at, updated_at) VALUES('pending-run','x','message.send','{}','pending_approval','external','ask',?,?)",
            (OLD, OLD),
        )
        self.conn.execute(
            "INSERT INTO run_steps(id, run_id, seq, kind, payload, prev_hash, hash, created_at) VALUES('step-old','old-run',0,'executed','{}','','hash',?)",
            (OLD,),
        )
        self.conn.execute(
            "INSERT INTO run_steps(id, run_id, seq, kind, payload, prev_hash, hash, created_at) VALUES('step-pending','pending-run',0,'proposed','{}','','hash',?)",
            (OLD,),
        )
        self.conn.execute(
            "INSERT INTO events(id, ts, source, type, payload, hash) VALUES('evt-old', ?, 'x', 'y', '{}', 'h')",
            (OLD,),
        )
        self.conn.execute(
            "INSERT INTO events(id, ts, source, type, payload, hash) VALUES('evt-new', ?, 'x', 'y', '{}', 'h')",
            (NOW,),
        )
        self.conn.execute(
            "INSERT INTO audit(id, ts, actor, action, detail) VALUES('audit-old', ?, 'user', 'x', '{}')",
            (OLD,),
        )
        self.conn.execute(
            "INSERT INTO model_usage(id, ts, provider, model, task, ok) VALUES('mu-old', ?, 'p', 'm', 't', 1)",
            (OLD,),
        )
        self.conn.execute(
            "INSERT INTO connector_items(id, connector, kind, external_id, payload, ts) VALUES('ci-old','c','calendar','e','{}',?)",
            (OLD,),
        )
        self.conn.execute(
            "INSERT INTO messages(id, external_id, sender, subject, snippet, body, ts) VALUES('msg-old','e','a','s','sn','b',?)",
            (OLD,),
        )
        self.conn.execute(
            "INSERT INTO candidates(id, kind, title, score, urgency, actionability, novelty, created_at) VALUES('cand-old','t','t',1,1,1,1,?)",
            (OLD,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_stats(self):
        data = maintenance.stats(self.conn)
        self.assertEqual(data["counts"]["runs"], 2)
        self.assertGreater(data["db_bytes"], 0)

    def test_prune_removes_old_operational_rows_but_keeps_pending(self):
        removed = maintenance.prune(
            self.conn, older_than_days=30, keep_recent_runs=0
        )
        self.assertGreater(removed["events"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM runs WHERE id='old-run'").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM runs WHERE id='pending-run'").fetchone()[0], 1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM events WHERE id='evt-new'").fetchone()[0], 1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM events WHERE id='evt-old'").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM audit WHERE id='audit-old'").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM messages WHERE id='msg-old'").fetchone()[0], 0
        )

    def test_prune_keep_recent_runs(self):
        maintenance.prune(self.conn, older_than_days=30, keep_recent_runs=10)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM runs WHERE id='old-run'").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
