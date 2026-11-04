# MCP inheritance for subagents

This branch adds a per-server option to keep an MCP available to parent chats
without starting or exposing it in subagent sessions:

```toml
[mcp_servers.example]
command = "example-mcp"
enabled_in_subagents = false
```

The default is `true`, preserving existing behavior. Project configuration can
enable a globally disabled server while retaining the global worker opt-out.
An excluded server is absent from worker tools and required-server startup
validation; explicitly calling it in a worker does not start it. Parent clients
remain independent of worker cleanup. Workers must leave excluded MCP operations
to a parent or another permitted execution path.

The option requires this branch's backend. The unmodified
`rust-v0.165.0-alpha.1.2` release ignores the new field.

Desktop updates can regenerate `mcp_servers.node_repl` with only its managed
transport fields and `mergeStrategy: "replace"`. This backend retains an existing
explicit `enabled_in_subagents` value for that recognizable native Node REPL
refresh. Other replacement fields retain their normal semantics, including
removing stale transport environment settings. An explicit incoming policy wins;
clearing the exact policy key or deleting the server is still supported.
This compatibility rule does not add an opt-out when none was configured, and
does not apply to unrelated servers or custom Node REPL launchers.

## Build and isolated checks

The base is release commit `720fec862a608ae1e5878d1eecb04fb93192e93c`.
Use the repository's pinned Rust 1.99.1 toolchain and Python 3.11 or newer.
The native identity fixture supports Windows and Linux; qualification so far ran
on Windows only. Python needs no additional packages.

From `codex-rs`:

```sh
cargo build --locked -p codex-cli --bin codex
cargo test --locked -p codex-config --lib
```

Run MCP library tests, including the opt-in native process regressions. The
existing sandbox-capability test needs the full Codex CLI path as well:

```sh
export CODEX_MCP_SCOPE_TEST_PYTHON="$(command -v python3)"
export CARGO_BIN_EXE_codex="$PWD/target/debug/codex"
cargo test --locked -p codex-mcp --lib -- --include-ignored --test-threads=2
```

PowerShell equivalents, still from `codex-rs`:

```powershell
$env:CODEX_MCP_SCOPE_TEST_PYTHON = (Get-Command python).Source
$env:CARGO_BIN_EXE_codex = (Resolve-Path .\target\debug\codex.exe).Path
cargo test --locked -p codex-mcp --lib -- --include-ignored --test-threads=2
```

If using `CARGO_TARGET_DIR`, substitute its executable path. For a narrow repeat:

```sh
cargo test --locked -p codex-mcp --lib worker_scope_tests -- --ignored --nocapture --test-threads=1
```

From the repository root, test the actual full CLI through a private app-server:

```sh
python3 scripts/mcp_conformance/worker_scope_smoke.py \
  codex-rs/target/debug/codex --report /tmp/worker-scope-smoke.json
```

On Windows, use `python`, `codex.exe`, and a writable local report path. The
script isolates configuration discovery in a temporary Git project, disables
apps/plugins and inference authentication, uses an unreachable loopback model
provider, and never starts a model turn. It verifies configuration layering and
parent process identity across a private MCP refresh, then checks cleanup.
It never connects to an existing desktop backend. Do not commit local receipts
containing machine paths.

To check desktop transport refresh compatibility through a private backend:

```sh
python3 scripts/mcp_conformance/node_repl_refresh_regression.py \
  codex-rs/target/debug/codex --report /tmp/node-refresh.json
```

This checks retained opt-outs and opt-ins, explicit edits and deletion, ordered
batch edits, unrelated servers, custom launchers, and replacement of stale
transport environment fields. It starts no model turn, chat worker, or MCP.
Use `--baseline` against the earlier inheritance build to reproduce policy loss.
The runner uses synthetic data; installed desktop code and private configuration
are not needed or included.

The focused native tests use the app-server library test target. From `codex-rs`:

```sh
cargo test --locked -p codex-app-server --lib config_manager_service::tests:: -- --test-threads=2
```

## Recorded validation

On Windows, 2026-10-30:

- 379 `codex-config` library tests passed.
- 275 `codex-mcp` library tests passed, including both opt-in native regressions:
  654 unique tests total, zero failures and zero ignored in those final runs.
- Six disposable worker MCP runtimes did not start the parent-only server, and
  the parent's exact PID/creation identity survived every worker shutdown.
- Default worker inheritance, required-server handling, direct-call denial,
  and worker-owned process cleanup passed.
- Full CLI and standalone app-server builds, `codex-core` library compile check,
  regenerated configuration schema, Rust formatting and diff checks passed.
- Installed full CLI accepted desktop launch flags, preserved the global opt-out
  through project enabling, retained the same parent MCP through a scope-only
  refresh, and exited with no fake server left. A separate private read-only
  command/exec echo check passed.
- No model request, real MCP call or hardware access was used.

The release manifest and lockfile initially disagreed on 167 local workspace
package version labels. A separate build-only commit reconciles those labels;
third-party versions, sources, checksums and dependencies are unchanged.

### Node REPL refresh follow-up, 2026-11-04

The desktop startup initializer replaces the complete `mcp_servers.node_repl`
table with a managed transport value that omits `enabled_in_subagents`. Replaying
that replacement against inheritance commit
`9d9da6891a912f5c5ae3e09097165a5356660479` reproduced removal of the explicit
worker policy. Backend repair commit
`bc48240f9ad4840656a6b181906e1b7e2ac9340a` retains it during that recognizable
refresh while respecting explicit policy changes and removal.

Qualified on Windows with the pinned Rust 1.99.1 toolchain:

- The full `codex` CLI built successfully with `--locked`.
- All 48 configuration-manager library tests passed, including the two new native
  regression tests for retained policies and ordered batch edits.
- All 14 private-backend API replay cases passed against both the built CLI and
  a separately installed copy. They cover retained false/true policies, absent
  policy, explicit edits, custom launchers and arguments, Windows and POSIX
  command paths, unrelated servers, ordered set/clear, invalid-policy rejection,
  and server deletion. Stale transport environment values were still removed.
- The private MCP smoke test passed against both copies. The same mock parent
  process identity survived a private refresh, and no disposable MCP remained.
- Rust formatting, Python lint/format checks and Git whitespace checks passed.

The Cargo test driver reached a conservative free-space guard after linking its
library test executable. The two focused tests and all 48 configuration-manager
tests were then run directly from that executable and passed. The interrupted
Cargo invocation is not counted as a successful test command. Temporary build
outputs were subsequently removed after the installed copy passed its checks.

These checks used isolated configuration and synthetic fixtures. They made no
model requests, started no chat workers, and made no real MCP calls. No active
chat or MCP was restarted or reloaded. The new runtime was staged separately for
a future normal desktop launch; live activation and new-worker enforcement of
this follow-up remain unqualified. The earlier config/MCP library test counts
above are historical results, not a rerun on this follow-up. Native execution on
other operating systems and compatibility with changed future desktop refresh
payloads remain outside this qualification.

## Live qualification on the test computer

At the original 2026-10-30 qualification, live desktop activation was pending and
the source computer's active chats continued using its stock backend. The Node
REPL refresh follow-up above also awaits its own live activation check. Software
tests alone do not establish a live desktop rollout or explain every historical
subprocess exit.

1. Preserve that computer's backend and configuration. Build this branch's full
   `codex` CLI. The standalone `codex-app-server` executable is not interchangeable
   with the full CLI expected by the desktop launch command.
2. Choose the MCPs to exclude from workers and add only their
   `enabled_in_subagents = false` settings. Preserve existing enablement,
   transport, credentials, permission and project settings.
3. Finish active work and close the desktop app normally before selecting the
   candidate through its `CODEX_CLI_PATH` launch environment. The source Windows
   desktop build supports this override; review the test computer's installed
   launcher before assuming another version or platform does too. Use matching
   stock helper binaries where that desktop packaging requires them.
4. Verify the backend's executable path and SHA-256, rather than relying only on
   its version string: patched and stock binaries share the base version.
5. In a disposable test chat, verify parent access, worker tool absence, no new
   excluded MCP process, and parent identity survival when workers finish. Also
   check an ordinary inherited MCP and normal cleanup. Do not use active
   engineering documents or hardware for this test.
6. Restore the original future-start backend preference/configuration if needed.
   Guard against overwriting later edits; changing a preference does not replace
   the backend already serving live chats. Recheck compatibility after app updates.

This branch contains source, synthetic fixtures and reproducible checks.
Machine configurations, credentials, runtime binaries and local preservation
backups are not required to reproduce the fix.
