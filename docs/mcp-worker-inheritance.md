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

## Live qualification on the test computer

Live desktop activation is still pending. The source computer's active chats
continued using its stock backend; software tests alone do not establish a live
desktop rollout or explain every historical subprocess exit.

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
