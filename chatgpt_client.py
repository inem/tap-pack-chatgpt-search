"""ChatGPT global-search adapter with explicit profile-owned authentication."""

import json
import os
from pathlib import Path
import re
import ssl
import stat
import tempfile
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)


AUTH_FILES = {
    "authorization": "chatgpt.com.authorization",
    "cookie": "chatgpt.com.cookie",
    "user-agent": "chatgpt.com.user-agent",
}
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)


class SearchError(RuntimeError):
    def __init__(self, message, *, kind="remote", status_code=None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class _NoRedirect(HTTPRedirectHandler):
    """Never forward account credentials to a redirect-selected destination."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password):
        raise SearchError("base-url must be an HTTP(S) URL without credentials", kind="config")
    port = parsed.port
    return f"{parsed.scheme}://{parsed.hostname}" + (f":{port}" if port else "")


def validate_transport(config, grants):
    base_url = config.get("base-url")
    if not isinstance(base_url, str) or not base_url.endswith("/"):
        raise SearchError("base-url must end with /", kind="config")
    origin = _origin(base_url)
    allowed = grants.get("origins") if isinstance(grants, dict) else None
    if not isinstance(allowed, list) or origin not in allowed:
        raise SearchError(f"base-url origin is not granted to this pack: {origin}", kind="config")
    proxy = config.get("proxy")
    if not isinstance(proxy, str):
        raise SearchError("proxy must be a string", kind="config")
    if proxy:
        parsed = urlsplit(proxy)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise SearchError("proxy must be an HTTP(S) origin without credentials", kind="config")
    ca_file = config.get("ca-file")
    if not isinstance(ca_file, str):
        raise SearchError("ca-file must be a string", kind="config")
    if ca_file and not Path(ca_file).expanduser().is_file():
        raise SearchError(f"CA file does not exist: {ca_file}", kind="config")
    timeout = config.get("timeout-seconds")
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise SearchError("timeout-seconds must be between 1 and 120", kind="config")
    return base_url, proxy, ca_file, timeout


def _safe_secret(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise SearchError(
            f"missing auth file {path}; run `tap chatgpt auth import --from-dir DIRECTORY`",
            kind="auth",
        ) from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SearchError(f"auth file must be a regular file, not a symlink: {path}", kind="auth")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SearchError(f"auth file must be mode 0600: {path}", kind="auth")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SearchError(f"auth file is empty: {path}", kind="auth")
    return value


def read_auth(state_dir):
    auth_dir = Path(state_dir) / "auth"
    return {name: _safe_secret(auth_dir / filename) for name, filename in AUTH_FILES.items()}


def auth_status(state_dir):
    auth_dir = Path(state_dir) / "auth"
    files = {}
    complete = True
    now = time.time()
    for name, filename in AUTH_FILES.items():
        path = auth_dir / filename
        try:
            info = path.lstat()
            regular = stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            mode = stat.S_IMODE(info.st_mode)
            valid = regular and mode == 0o600 and info.st_size > 0
            files[name] = {
                "present": True, "valid": valid, "mode": f"{mode:04o}",
                "age_seconds": max(0, int(now - info.st_mtime)),
            }
        except FileNotFoundError:
            valid = False
            files[name] = {"present": False, "valid": False}
        complete = complete and valid
    return {"configured": complete, "files": files,
            "note": "file age is diagnostic only; the host cannot prove session freshness"}


def import_auth(state_dir, source_dir):
    source_dir = Path(source_dir).expanduser().resolve()
    values = {name: _safe_secret(source_dir / filename)
              for name, filename in AUTH_FILES.items()}
    destination = Path(state_dir) / "auth"
    if destination.is_symlink():
        raise SearchError(f"auth directory must not be a symlink: {destination}", kind="auth")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.chmod(0o700)
    for name, filename in AUTH_FILES.items():
        descriptor, temporary_name = tempfile.mkstemp(prefix=".auth-", dir=destination)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(values[name] + "\n")
            temporary.chmod(0o600)
            temporary.replace(destination / filename)
        finally:
            temporary.unlink(missing_ok=True)
    return auth_status(state_dir)


def search_request(query, limit=10, sources=("conversation",), cursor=None, query_id=None):
    if not isinstance(query, str) or not query.strip():
        raise SearchError("search query cannot be empty", kind="usage")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise SearchError("limit must be between 1 and 100", kind="usage")
    if not isinstance(sources, (list, tuple)) or not sources:
        raise SearchError("at least one search source is required", kind="usage")
    allowed = ("conversation", "project", "library")
    if any(source not in allowed for source in sources):
        raise SearchError("sources must be conversation, project, or library", kind="usage")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise SearchError("cursor must be a non-empty string", kind="usage")
    if query_id is not None and (not isinstance(query_id, str) or not UUID.fullmatch(query_id)):
        raise SearchError("query_id must be a UUID", kind="usage")
    requests = []
    for source in dict.fromkeys(sources):
        item = {"type": source}
        if source == "library":
            item["filters"] = {
                "lanes": ["image", "document", "folder"], "providers": ["native"],
            }
        requests.append(item)
    result = {
        "query": query.strip(), "limit": limit, "query_id": query_id or str(uuid.uuid4()),
        "entrypoint": "global_search", "source_requests": requests,
    }
    if cursor is not None:
        result["cursor"] = cursor
    return result


def _error_message(raw):
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return (raw or "empty response").strip()[:240]
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict) and detail.get("message"):
            return str(detail["message"])
        if isinstance(detail, str):
            return detail
        if payload.get("message"):
            return str(payload["message"])
    return json.dumps(payload, ensure_ascii=False)[:240]


def search(payload, context):
    config = context["config"]
    base_url, proxy, ca_file, timeout = validate_transport(config, context["grants"])
    auth = read_auth(context["state_dir"])
    request = Request(
        base_url + "global/search",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={
            "Authorization": auth["authorization"], "Cookie": auth["cookie"],
            "User-Agent": auth["user-agent"], "Content-Type": "application/json",
            "Origin": _origin(base_url), "Referer": _origin(base_url) + "/",
        },
    )
    handlers = []
    if proxy:
        handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(ProxyHandler({}))
    if urlsplit(base_url).scheme == "https":
        ssl_context = ssl.create_default_context(cafile=str(Path(ca_file).expanduser()) if ca_file else None)
        handlers.append(HTTPSHandler(context=ssl_context))
    opener = build_opener(*handlers, _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as error:
        try:
            raw = error.read().decode("utf-8", errors="replace")
        except OSError:
            raw = ""
        raise SearchError(
            f"ChatGPT returned HTTP {error.code}: {_error_message(raw)}",
            status_code=error.code,
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise SearchError(f"cannot reach ChatGPT: {getattr(error, 'reason', error)}") from error
    if status != 200:
        raise SearchError(f"ChatGPT returned HTTP {status}: {_error_message(raw)}", status_code=status)
    try:
        result = json.loads(raw)
    except ValueError as error:
        raise SearchError("ChatGPT returned a non-JSON response") from error
    if (not isinstance(result, dict) or not isinstance(result.get("items"), list)
            or any(not isinstance(item, dict) for item in result["items"])):
        raise SearchError("search returned no valid items array")
    return result
