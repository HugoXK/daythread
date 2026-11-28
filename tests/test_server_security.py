import logging
import os
import shlex
import stat
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from daythread import obs
from daythread.db import connect, init_db
from daythread.server import ApiServer, ensure_token


class TokenPermissionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db_path = self.home / "db.sqlite"
        conn = connect(self.db_path)
        init_db(conn)
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_serve_corrects_existing_token_and_home_modes(self):
        os.chmod(self.home, 0o755)
        token_path = self.home / "api_token"
        token_path.write_text("pre-existing-token", encoding="utf-8")
        os.chmod(token_path, 0o644)
        server = ApiServer(self.home, self.db_path, port=0).start()
        try:
            self.assertEqual(server.token, "pre-existing-token")
            self.assertEqual(stat.S_IMODE(token_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        finally:
            server.stop()

    def test_serve_fails_closed_when_token_chmod_fails(self):
        token_path = self.home / "api_token"
        real_chmod = os.chmod

        def fake_chmod(path, mode):
            if Path(path) == token_path:
                raise PermissionError("operation not permitted")
            return real_chmod(path, mode)

        with mock.patch("daythread.server.os.chmod", side_effect=fake_chmod):
            with self.assertRaises(RuntimeError) as caught:
                ApiServer(self.home, self.db_path, port=0)
        message = str(caught.exception)
        self.assertIn(str(token_path), message)
        self.assertIn("chmod 600 {}".format(token_path), message)

    def test_serve_fails_closed_when_home_chmod_fails(self):
        real_chmod = os.chmod

        def fake_chmod(path, mode):
            if Path(path) == self.home:
                raise PermissionError("operation not permitted")
            return real_chmod(path, mode)

        with mock.patch("daythread.server.os.chmod", side_effect=fake_chmod):
            with self.assertRaises(RuntimeError) as caught:
                ApiServer(self.home, self.db_path, port=0)
        message = str(caught.exception)
        self.assertIn(str(self.home), message)
        self.assertIn("chmod 700 {}".format(self.home), message)

    def test_fail_closed_quotes_home_path_with_spaces(self):
        home = Path(self.tmp.name) / "home with space"
        home.mkdir()
        real_chmod = os.chmod

        def fake_chmod(path, mode):
            if Path(path) == home:
                raise PermissionError("operation not permitted")
            return real_chmod(path, mode)

        with mock.patch("daythread.server.os.chmod", side_effect=fake_chmod):
            with self.assertRaises(RuntimeError) as caught:
                ensure_token(home)
        message = str(caught.exception)
        command = "chmod 700 {}".format(shlex.quote(str(home)))
        self.assertIn(command, message)
        self.assertEqual(shlex.split(command), ["chmod", "700", str(home)])

    def test_fail_closed_quotes_token_path_with_spaces(self):
        home = Path(self.tmp.name) / "home with space"
        home.mkdir()
        token_path = home / "api_token"
        real_chmod = os.chmod

        def fake_chmod(path, mode):
            if Path(path) == token_path:
                raise PermissionError("operation not permitted")
            return real_chmod(path, mode)

        with mock.patch("daythread.server.os.chmod", side_effect=fake_chmod):
            with self.assertRaises(RuntimeError) as caught:
                ensure_token(home)
        message = str(caught.exception)
        command = "chmod 600 {}".format(shlex.quote(str(token_path)))
        self.assertIn(command, message)
        self.assertEqual(shlex.split(command), ["chmod", "600", str(token_path)])


class ServerHeaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db_path = self.home / "db.sqlite"
        conn = connect(self.db_path)
        init_db(conn)
        conn.close()
        self.server = ApiServer(self.home, self.db_path, port=0).start()
        self.token = self.server.token

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def _get(self, path):
        url = "http://127.0.0.1:{}{}".format(self.server.port, path)
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()

    def _assert_security_headers(self, headers):
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn(
            "frame-ancestors 'none'", headers.get("Content-Security-Policy", "")
        )
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_ui_security_headers(self):
        status, headers, body = self._get("/ui")
        self.assertEqual(status, 200)
        self.assertIn(b"Daythread", body)
        self._assert_security_headers(headers)

    def test_api_security_headers(self):
        status, headers, _ = self._get("/v1/health")
        self.assertEqual(status, 200)
        self._assert_security_headers(headers)

    def test_access_log_records_headers_without_token(self):
        log_path = self.home / "access.log"
        logger = obs.get_logger()
        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            status, _, _ = self._get("/ui")
            self.assertEqual(status, 200)
            handler.flush()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            handler.close()

        logged = log_path.read_text(encoding="utf-8")
        self.assertIn("X-Frame-Options", logged)
        self.assertIn("DENY", logged)
        self.assertIn("frame-ancestors 'none'", logged)
        self.assertIn("nosniff", logged)
        self.assertIn("no-referrer", logged)
        self.assertNotIn(self.token, logged)


if __name__ == "__main__":
    unittest.main()
