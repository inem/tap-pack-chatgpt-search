#!/usr/bin/env python3
"""Run one live read-only search and discard all result content."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run(command, *, stdout=subprocess.PIPE):
    return subprocess.run(command, text=True, stdout=stdout, stderr=subprocess.PIPE)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--auth-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    core, artifact, auth_dir = args.core.resolve(), args.artifact.resolve(), args.auth_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="chatgpt-search-live-") as temporary:
        profile = Path(temporary) / "profile"
        base = [sys.executable, "-B", str(core / "tap"), "--profile", str(profile)]
        steps = [
            ("install", [*base, "pack", "install", str(artifact)]),
            ("enable", [*base, "pack", "enable", "chatgpt.search", "--version", "0.1.0",
                        "--grant-origin", "https://chatgpt.com",
                        "--grant-capability", "command.execute"]),
            ("auth_import", [*base, "chatgpt", "auth", "import", "--from-dir", str(auth_dir)]),
        ]
        outcomes = {}
        for name, command in steps:
            result = run(command)
            outcomes[name] = result.returncode
            if result.returncode:
                report = {"live_search": "not run", "failed_step": name,
                          "exit_code": result.returncode, "result_content": "not saved"}
                break
        else:
            result = run([*base, "chatgpt", "search",
                          "tap-live-verification-2026-09-08", "--limit", "1", "--json"],
                         stdout=subprocess.DEVNULL)
            outcomes["search"] = result.returncode
            if result.returncode and result.stderr:
                print(result.stderr.strip(), file=sys.stderr)
            report = {"live_search": "passed" if result.returncode == 0 else "failed",
                      "exit_code": result.returncode, "steps": outcomes,
                      "result_content": "discarded, not saved"}
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return 0 if report.get("live_search") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
