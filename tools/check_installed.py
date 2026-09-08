#!/usr/bin/env python3
"""Installed-artifact check with anonymized loopback search responses."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parent.parent


class SearchHandler(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size))
        self.__class__.requests.append({
            "path": self.path,
            "query": payload.get("query"),
            "auth": self.headers.get("Authorization") == "Bearer synthetic",
            "cookie": self.headers.get("Cookie") == "session=synthetic",
        })
        body = json.dumps({
            "items": [{
                "title": "Synthetic result", "source_type": "conversation",
                "match_kind": "content", "snippet": "An anonymized fixture snippet",
                "payload": {
                    "conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                },
            }],
            "partial_results": False,
            "source_statuses": [{"source_type": "conversation", "status": "ok"}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def run(command, *, expected=0, environment=None):
    result = subprocess.run(command, text=True, capture_output=True, env=environment)
    if result.returncode != expected:
        raise RuntimeError(
            f"expected exit {expected}, got {result.returncode}: {' '.join(map(str, command))}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")
    return result


def write_auth(directory):
    directory.mkdir(mode=0o700)
    values = {
        "chatgpt.com.authorization": "Bearer synthetic",
        "chatgpt.com.cookie": "session=synthetic",
        "chatgpt.com.user-agent": "TAP installed fixture/1",
    }
    for name, value in values.items():
        path = directory / name
        path.write_text(value + "\n")
        path.chmod(0o600)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    core = args.core.resolve()
    tap = core / "tap"
    SearchHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), SearchHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory(prefix="chatgpt-search-installed-") as temporary:
            temporary = Path(temporary)
            source = temporary / "source"
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            manifest_path = source / "pack.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["access"]["origins"] = [origin]
            manifest["config"]["base-url"]["default"] = origin + "/backend-api/"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            profile = temporary / "profile"
            artifact = temporary / "chatgpt.search-0.1.1.tap-pack"
            environment = {**os.environ, "PYTHONPATH": str(core),
                           "PYTHONPYCACHEPREFIX": str(temporary / "pycache"),
                           "HOME": str(temporary)}
            run([sys.executable, "-B", "-m", "tap_core.pack_store", "build", str(source),
                 "--output", str(artifact)], environment=environment)
            base = [sys.executable, "-B", str(tap), "--profile", str(profile)]
            run([*base, "pack", "install", str(artifact)], environment=environment)
            run([*base, "pack", "enable", "chatgpt.search", "--version", "0.1.1",
                 "--grant-origin", origin, "--grant-capability", "command.execute"],
                environment=environment)
            self_help = run([*base, "chatgpt", "search", "--help"], environment=environment)
            root_help = run([*base, "--help"], environment=environment)
            help_no_request = len(SearchHandler.requests) == 0
            missing = run([*base, "chatgpt", "search", "fixture query", "--json"],
                          expected=3, environment=environment)
            missing_auth_clear = "auth import" in missing.stderr and len(SearchHandler.requests) == 0
            auth_source = temporary / "auth-source"
            write_auth(auth_source)
            imported = run([*base, "chatgpt", "auth", "import", "--from-dir", str(auth_source)],
                           environment=environment)
            searched = run([*base, "chatgpt", "search", "alpha;$(not-a-shell)", "--json"],
                           environment=environment)
            result = json.loads(searched.stdout)
            request = SearchHandler.requests[-1]

            manifest["version"] = "0.1.2"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            update_artifact = temporary / "chatgpt.search-0.1.2.tap-pack"
            run([sys.executable, "-B", "-m", "tap_core.pack_store", "build", str(source),
                 "--output", str(update_artifact)], environment=environment)
            updated = run([*base, "pack", "update", str(update_artifact)], environment=environment)
            updated_help = run([*base, "chatgpt", "search", "--help"], environment=environment)
            dry = run([*base, "chatgpt", "search", "dry", "--dry-run"], environment=environment)
            request_count_before_disable = len(SearchHandler.requests)
            disabled = run([*base, "pack", "disable", "chatgpt.search"], environment=environment)
            unavailable = run([*base, "chatgpt", "search", "disabled"], expected=2,
                              environment=environment)
            uninstalled = run([*base, "pack", "uninstall", "chatgpt.search"], environment=environment)
            retained_auth = (profile / "state/packs/chatgpt.search/auth/chatgpt.com.cookie").is_file()
            versions_dir = profile / "packs/chatgpt.search/versions"
            code_removed = not versions_dir.exists() or not any(versions_dir.iterdir())
            report = {
                "evidence": "installed artifact with anonymized loopback response",
                "core": str(core),
                "pack": {"id": "chatgpt.search", "versions": ["0.1.1", "0.1.2"]},
                "assertions": {
                    "bare_profile_without_capture_config": not (profile / "profile.json").exists(),
                    "root_help_lists_command_and_owner": "chatgpt search" in root_help.stdout
                    and "chatgpt.search@0.1.1" in root_help.stdout,
                    "command_help_is_declarative": "help does not run provider code" in self_help.stdout,
                    "help_made_no_request": help_no_request,
                    "missing_auth_exit_3_and_clear": missing_auth_clear,
                    "auth_import_did_not_print_values": "synthetic" not in imported.stdout,
                    "search_exit_0_and_one_item": len(result.get("items", [])) == 1,
                    "argv_not_shell_interpreted": request["query"] == "alpha;$(not-a-shell)",
                    "credentials_reached_only_fixture_origin": request["auth"] and request["cookie"],
                    "update_selected_0_1_2": '"version": "0.1.2"' in updated.stdout
                    and "chatgpt.search@0.1.2" in updated_help.stdout,
                    "dry_run_made_no_request": len(SearchHandler.requests) == request_count_before_disable,
                    "disable_removed_command": "invalid choice" in unavailable.stderr,
                    "disable_behavior_documented": "command discovery" in disabled.stdout,
                    "uninstall_removed_both_versions": all(
                        version in uninstalled.stdout for version in ("0.1.1", "0.1.2")),
                    "installed_code_removed": code_removed,
                    "profile_auth_retained": retained_auth,
                },
                "not_claimed": ["live ChatGPT", "credential freshness", "clean Mac", "stable API"],
            }
            failed = [name for name, value in report["assertions"].items() if value is not True]
            if failed:
                raise RuntimeError("failed assertions: " + ", ".join(failed))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
