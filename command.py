#!/usr/bin/env python3
"""CLI presentation for the ChatGPT search pack."""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlencode

from chatgpt_client import (SearchError, auth_status, import_auth, search,
                            search_request)


def context():
    try:
        value = json.loads(os.environ["TAP_COMMAND_CONTEXT"])
    except (KeyError, ValueError) as error:
        raise SearchError("TAP_COMMAND_CONTEXT is missing or invalid", kind="config") from error
    if (not isinstance(value, dict) or value.get("command_api") != 1
            or not isinstance(value.get("path"), list)
            or not isinstance(value.get("state_dir"), str)):
        raise SearchError("unsupported command context", kind="config")
    return value


def clean(value):
    return " ".join("".join(character if character.isprintable() else " "
                            for character in str(value or "")).split())


def render(result, query, query_id):
    for index, item in enumerate(result["items"], 1):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        print(f"{index}. {clean(item.get('title') or '(untitled)')}")
        print(f"   source: {clean(item.get('source_type'))} · match: {clean(item.get('match_kind'))}")
        conversation_id = payload.get("conversation_id")
        message_id = payload.get("message_id")
        if conversation_id:
            print("   conversation_id: " + clean(conversation_id))
            if message_id:
                print("   message_id: " + clean(message_id))
            parameters = {"historySearchQuery": query, "src": "history_search"}
            if message_id:
                parameters["messageId"] = message_id
            print("   https://chatgpt.com/c/" + clean(conversation_id) + "?" + urlencode(parameters))
        elif item.get("id"):
            print("   id: " + clean(item["id"]))
        snippet = clean(item.get("snippet"))
        if snippet:
            print("   " + (snippet[:397] + "..." if len(snippet) > 400 else snippet))
        print()
    if not result["items"]:
        print("No matches returned.")
    if result.get("partial_results"):
        print("Partial results: some sources did not finish successfully.", file=sys.stderr)
    statuses = result.get("source_statuses")
    for status in statuses if isinstance(statuses, list) else []:
        if isinstance(status, dict) and status.get("status") not in (None, "ok"):
            suffix = " (" + clean(status["error_code"]) + ")" if status.get("error_code") else ""
            print(f"Source {clean(status.get('source_key') or status.get('source_type'))}: "
                  f"{clean(status.get('status'))}{suffix}", file=sys.stderr)
    if result.get("cursor"):
        print("Next page: repeat this query and sources with --cursor and --query-id.", file=sys.stderr)
        print("cursor: " + json.dumps(result["cursor"], ensure_ascii=False), file=sys.stderr)
        print("query_id: " + query_id, file=sys.stderr)


def search_main(argv, command_context):
    parser = argparse.ArgumentParser(prog="tap chatgpt search", add_help=False)
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--sources", default="conversation")
    parser.add_argument("--cursor")
    parser.add_argument("--query-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    sources = tuple(source.strip() for source in args.sources.split(","))
    try:
        payload = search_request(args.query, args.limit, sources, args.cursor, args.query_id)
    except SearchError as error:
        parser.error(str(error))
    if args.dry_run:
        print(json.dumps({"method": "POST", "url": command_context["config"]["base-url"] + "global/search",
                          "body": payload}, ensure_ascii=False, indent=2))
        return 0
    result = search(payload, command_context)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        render(result, payload["query"], payload["query_id"])
    return 0


def auth_import_main(argv, command_context):
    parser = argparse.ArgumentParser(prog="tap chatgpt auth import", add_help=False)
    parser.add_argument("--from-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = import_auth(command_context["state_dir"], args.from_dir)
    print("ChatGPT auth imported into private pack state (values not displayed).")
    print(json.dumps(result, indent=2))
    return 0


def auth_status_main(argv, command_context):
    parser = argparse.ArgumentParser(prog="tap chatgpt auth status", add_help=False)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = auth_status(command_context["state_dir"])
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        state = "configured" if result["configured"] else "not configured"
        print("ChatGPT auth: " + state)
        for name, item in result["files"].items():
            detail = (f"mode {item['mode']}, age {item['age_seconds']}s"
                      if item.get("present") else "missing")
            print(f"  {name}: {detail}")
        print(result["note"])
    return 0 if result["configured"] else 3


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        command_context = context()
        path = tuple(command_context["path"])
        if path == ("chatgpt", "search"):
            return search_main(argv, command_context)
        if path == ("chatgpt", "auth", "import"):
            return auth_import_main(argv, command_context)
        if path == ("chatgpt", "auth", "status"):
            return auth_status_main(argv, command_context)
        raise SearchError("unknown command path", kind="config")
    except SearchError as error:
        print("tap chatgpt: " + str(error), file=sys.stderr)
        return 3 if error.kind == "auth" else 4


if __name__ == "__main__":
    raise SystemExit(main())
