import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chatgpt_client import (AUTH_FILES, SearchError, auth_status, import_auth,
                            read_auth, search, search_request, validate_transport)
import command


class ChatGPTSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="chatgpt-search-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / "state"
        self.source = self.root / "source"
        self.source.mkdir()
        for name, filename in AUTH_FILES.items():
            path = self.source / filename
            path.write_text({
                "authorization": "Bearer synthetic",
                "cookie": "session=synthetic",
                "user-agent": "TAP fixture/1",
            }[name])
            path.chmod(0o600)

    def context(self, path=("chatgpt", "search")):
        return {
            "command_api": 1, "path": list(path), "state_dir": str(self.state),
            "config": {"base-url": "https://chatgpt.com/backend-api/", "proxy": "",
                       "ca-file": "", "timeout-seconds": 30},
            "grants": {"origins": ["https://chatgpt.com"],
                       "capabilities": ["command.execute"], "dependencies": {}},
        }

    def test_request_shape_pagination_and_sources(self):
        query_id = "11111111-1111-4111-8111-111111111111"
        request = search_request("  alpha beta ", 20,
                                 ("conversation", "project", "library", "conversation"),
                                 "opaque-cursor", query_id)
        self.assertEqual(request["query"], "alpha beta")
        self.assertEqual(request["query_id"], query_id)
        self.assertEqual(request["cursor"], "opaque-cursor")
        self.assertEqual([item["type"] for item in request["source_requests"]],
                         ["conversation", "project", "library"])
        self.assertIn("filters", request["source_requests"][2])
        for invalid in (0, 101, True):
            with self.assertRaisesRegex(SearchError, "limit"):
                search_request("query", invalid)

    def test_auth_import_permissions_status_and_missing_refusal(self):
        with self.assertRaisesRegex(SearchError, "missing auth file"):
            read_auth(self.state)
        result = import_auth(self.state, self.source)
        self.assertTrue(result["configured"])
        for filename in AUTH_FILES.values():
            path = self.state / "auth" / filename
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn("synthetic", json.dumps(auth_status(self.state)))
        (self.state / "auth" / AUTH_FILES["cookie"]).chmod(0o644)
        with self.assertRaisesRegex(SearchError, "0600"):
            read_auth(self.state)

    def test_transport_origin_must_be_granted(self):
        context = self.context()
        validate_transport(context["config"], context["grants"])
        context["config"]["base-url"] = "https://example.test/backend-api/"
        with self.assertRaisesRegex(SearchError, "not granted"):
            validate_transport(context["config"], context["grants"])

    def test_transport_failure_is_not_automatically_retried(self):
        import_auth(self.state, self.source)
        opener = Mock()
        opener.open.side_effect = URLError("synthetic offline")
        with patch("chatgpt_client.build_opener", return_value=opener):
            with self.assertRaisesRegex(SearchError, "cannot reach ChatGPT"):
                search(search_request("one attempt"), self.context())
        self.assertEqual(opener.open.call_count, 1)

    def test_redirects_never_forward_credentials(self):
        received = []

        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"items": []}')

            def log_message(self, *_args):
                pass

        destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        destination_thread = threading.Thread(target=destination.serve_forever, daemon=True)
        destination_thread.start()
        def cleanup_destination():
            destination.shutdown()
            destination.server_close()
            destination_thread.join(timeout=5)
        self.addCleanup(cleanup_destination)

        for status in (302, 303):
            class Origin(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.send_response(status)
                    self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/sink")
                    self.end_headers()

                def log_message(self, *_args):
                    pass

            origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
            origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
            origin_thread.start()
            try:
                import_auth(self.state, self.source)
                context = self.context()
                context["config"]["base-url"] = f"http://127.0.0.1:{origin.server_port}/"
                context["grants"]["origins"] = [f"http://127.0.0.1:{origin.server_port}"]
                with self.assertRaisesRegex(SearchError, f"HTTP {status}"):
                    search(search_request("redirect"), context)
            finally:
                origin.shutdown()
                origin.server_close()
                origin_thread.join(timeout=5)
        self.assertEqual(received, [])

    def test_dry_run_does_not_read_auth_or_open_network(self):
        context = self.context()
        from contextlib import redirect_stdout
        import io
        with patch.dict(os.environ, {"TAP_COMMAND_CONTEXT": json.dumps(context)}), \
                patch("command.search", side_effect=AssertionError("network must not run")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(command.main(["dry run", "--dry-run"]), 0)
        self.assertFalse((self.state / "auth").exists())

    def test_response_rendering_sanitizes_terminal_controls(self):
        result = {"items": [{
            "title": "Synthetic\x1b[31m title", "source_type": "conversation",
            "match_kind": "content", "snippet": "neutral snippet",
            "payload": {"conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
        }], "partial_results": False}
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        with redirect_stdout(output):
            command.render(result, "query", "11111111-1111-4111-8111-111111111111")
        self.assertNotIn("\x1b", output.getvalue())
        self.assertIn("Synthetic [31m title", output.getvalue())


if __name__ == "__main__":
    unittest.main()
