# TAP ChatGPT Search pack

An independently installable TAP command pack for ChatGPT global search. It does
not read captured records and does not require a reader, organizer, page, Hub or
WebSocket. The default transport connects directly to `https://chatgpt.com`.

The command protocol is language-neutral `process-argv-v1`; this version selects
the explicit `host-python` binding. Search is an out-of-page HTTP adapter and uses
only Python's standard library, so a bare command profile does not gain a Bun
runtime requirement. This does not make Python the required language of future
packs: a versioned `host-bun` binding can use the same argv/context protocol once
Core has a concrete Bun executable inventory instead of implicit PATH discovery.

This pack uses ChatGPT's observed private web endpoint, not a stable public API.
Compatibility can change without notice. Search is read-only by command intent,
but the imported browser credentials carry the authority of the account session;
TAP's manifest grant is not an OS sandbox or a reduced-scope ChatGPT token.

## Use

With an installed TAP Core containing command API v1 and `pack add`:

```sh
tap pack add inem/tap-pack-chatgpt-search@0.1.1
tap chatgpt search "release notes"
```

The first search automatically migrates valid protected credentials from the
legacy `~/.tap/auth` directory when they are present. It says where they came
from without printing their values. Later searches use the profile-owned copy.
There is no separate required `connect` step.

If no compatible legacy authorization exists, search stops without retrying and
gives the explicit manual import command. This release does not claim a new-user
browser login or OAuth flow.

`pack add` shows the exact version, publisher, purpose and requested access
before enabling local executable code. The `@0.1.1` suffix is required while
this release remains a prerelease; stable releases are selected by default.

## Developer installation

Use a TAP Core checkout containing command API v1. A command-only pack may use a
new profile directory; no `tap install` or capture service is required.

```sh
CORE=/absolute/path/to/tap-core
PROFILE="$HOME/.tap-core-chatgpt-search"

curl -fL \
  https://github.com/inem/tap-pack-chatgpt-search/releases/download/v0.1.1/chatgpt.search-0.1.1.tap-pack \
  -o /tmp/chatgpt.search-0.1.1.tap-pack
```

Or build the same deterministic artifact from source:

```sh
CORE=/absolute/path/to/tap-core
PROFILE="$HOME/.tap-core-chatgpt-search"

PYTHONPATH="$CORE" python3 -B -m tap_core.pack_store build . \
  --output /tmp/chatgpt.search-0.1.1.tap-pack
```

Enable it and confirm declarative discovery:

```sh
"$CORE/tap" --profile "$PROFILE" pack install /tmp/chatgpt.search-0.1.1.tap-pack
"$CORE/tap" --profile "$PROFILE" pack enable chatgpt.search \
  --version 0.1.1 \
  --grant-origin https://chatgpt.com \
  --grant-capability command.execute
"$CORE/tap" --profile "$PROFILE" --help
"$CORE/tap" --profile "$PROFILE" chatgpt search --help
```

## Advanced auth import

The pack expects three regular, non-symlink files with mode `0600`:

- `chatgpt.com.authorization`
- `chatgpt.com.cookie`
- `chatgpt.com.user-agent`

Import a directory containing those files. Values are copied to the pack's
private profile state and are never printed. The legacy auth-harvest output
directory (`~/.tap/auth`) is one compatible producer, but running capture is not
required after import. You may use another explicit producer that creates the
same protected files. Do not put credential values in command arguments.

```sh
"$CORE/tap" --profile "$PROFILE" chatgpt auth import --from-dir "$HOME/.tap/auth"
"$CORE/tap" --profile "$PROFILE" chatgpt auth status
```

File age is reported only as a diagnostic. It cannot prove that a ChatGPT
session is still valid. Re-import after the source session refreshes. Disabling
or uninstalling the pack retains profile-owned auth/state; remove that directory
separately if you intend to erase credentials.

## Search

```sh
"$CORE/tap" --profile "$PROFILE" chatgpt search "release notes"
"$CORE/tap" --profile "$PROFILE" chatgpt search "diagram" \
  --sources conversation,project,library --limit 20 --json
"$CORE/tap" --profile "$PROFILE" chatgpt search "preview" --dry-run
```

One invocation requests one page. `cursor` and `query_id` are returned for an
explicit next-page call; there is no automatic pagination or retry. Exit codes:
`0` success, `2` argument error, `3` missing/unsafe auth, `4` transport, remote,
response or pack-configuration failure. Partial source results remain a success
and are called out on stderr.

The optional `proxy` and `ca-file` config fields support an explicitly selected
proxy transport. They are empty by default, so the installed search path has no
runtime dependency on TAP capture or the legacy proxy.

## Lifecycle

```sh
"$CORE/tap" --profile "$PROFILE" pack update /path/to/chatgpt.search-0.2.0.tap-pack
"$CORE/tap" --profile "$PROFILE" pack rollback chatgpt.search
"$CORE/tap" --profile "$PROFILE" pack disable chatgpt.search
"$CORE/tap" --profile "$PROFILE" pack uninstall chatgpt.search
```

New invocations use the selected immutable version. The host holds a profile
lease for the lifetime of an invocation, so update, rollback, disable and
uninstall fail clearly while it is running. They can be retried after it exits.

## Evidence boundary

`tests/test_chatgpt_search.py` uses anonymized responses and a loopback server.
`tools/check_installed.py` builds and installs an artifact in a temporary bare
profile and records only structural assertions, never credential or result text.
That is fixture and installed-artifact evidence, not current live ChatGPT or a
clean-Mac release acceptance.
