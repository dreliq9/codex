#!/usr/bin/env python3
"""Qualify a full Codex CLI with private configuration and a disposable stdio MCP.

No model turn, live desktop connection, real MCP, credentials or hardware is used.
The native identity fixture currently supports Windows and Linux.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codex", type=Path, help="Full candidate Codex CLI executable")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--report", type=Path, help="Write a local JSON receipt outside the checkout"
    )
    args = parser.parse_args()
    if os.name != "nt" and not sys.platform.startswith("linux"):
        parser.error(
            "The disposable native process identity fixture supports Windows and Linux"
        )
    binary, python = args.codex.resolve(strict=True), args.python.resolve(strict=True)
    fixture_source = (
        Path(__file__).resolve().parents[2]
        / "codex-rs/codex-mcp/src/runtime/worker_scope_server.py"
    )
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    record = {
        "started": dt.datetime.now(dt.timezone.utc).isoformat(),
        "binary": str(binary),
        "binary_sha256": digest,
        "status": "FAIL",
        "model_turns": 0,
        "live_host_connection": False,
        "real_mcp_calls": False,
        "real_configuration_changes": False,
    }
    with tempfile.TemporaryDirectory(prefix="codex-worker-scope-") as temporary:
        root = Path(temporary)
        home, project = root / "home", root / "project"
        home.mkdir()
        project.mkdir()
        # Bound project configuration discovery before creating the private thread.
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        fixture = root / "worker_scope_server.py"
        shutil.copyfile(fixture_source, fixture)
        events_path = root / "events.jsonl"
        config = "\n".join(
            [
                'model = "isolated-test-model"',
                'model_provider = "isolated_test"',
                'approval_policy = "never"',
                'sandbox_mode = "read-only"',
                "[features]",
                "apps = false",
                "plugins = false",
                "[analytics]",
                "enabled = false",
                "[model_providers.isolated_test]",
                'name = "No-inference test"',
                'base_url = "http://127.0.0.1:1"',
                'wire_api = "responses"',
                "requires_openai_auth = false",
                "[mcp_servers.parent_only]",
                "command = " + json.dumps(str(python)),
                "args = "
                + json.dumps(
                    ["-B", str(fixture), str(events_path), "worker-scope-smoke"]
                ),
                "enabled = false",
                "enabled_in_subagents = false",
                "required = true",
                "startup_timeout_sec = 10",
                "[projects." + json.dumps(str(project)) + "]",
                'trust_level = "trusted"',
                "",
            ]
        )
        config_path = home / "config.toml"
        config_path.write_text(config, encoding="utf-8")
        (project / ".codex").mkdir()
        (project / ".codex/config.toml").write_text(
            "[mcp_servers.parent_only]\nenabled = true\n", encoding="utf-8"
        )
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "COMSPEC",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "HOME",
            "LANG",
            "LC_ALL",
        }
        environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        environment.update(
            CODEX_HOME=str(home), TEMP=str(root), TMP=str(root), TMPDIR=str(root)
        )
        messages = queue.Queue()
        counter = 0
        with (root / "stderr.log").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                [
                    str(binary),
                    "-c",
                    "features.code_mode_host=true",
                    "app-server",
                    "--analytics-default-enabled",
                    "-c",
                    "features.prefer_mxc=true",
                ],
                cwd=project,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                encoding="utf-8",
            )

            def read():
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        pass
                messages.put({"closed": True})

            threading.Thread(target=read, daemon=True).start()

            def send(value):
                process.stdin.write(json.dumps(value) + "\n")
                process.stdin.flush()

            def call(method, params):
                nonlocal counter
                counter += 1
                identifier = counter
                send({"id": identifier, "method": method, "params": params})
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    value = messages.get(timeout=max(0.1, deadline - time.monotonic()))
                    if value.get("closed"):
                        raise RuntimeError("Disposable backend closed")
                    if value.get("id") == identifier:
                        if "error" in value:
                            raise RuntimeError(json.dumps(value["error"]))
                        return value.get("result")
                raise TimeoutError(method)

            def identity(response):
                if isinstance(response, dict):
                    if isinstance(response.get("text"), str):
                        try:
                            value = json.loads(response["text"])
                            if (
                                isinstance(value, dict)
                                and value.get("owner") == "worker-scope-smoke"
                            ):
                                return value
                        except ValueError:
                            pass
                    children = response.values()
                elif isinstance(response, list):
                    children = response
                else:
                    return None
                for child in children:
                    found = identity(child)
                    if found:
                        return found
                return None

            try:
                call(
                    "initialize",
                    {
                        "clientInfo": {"name": "worker-scope-smoke", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                send({"method": "initialized"})
                loaded = call(
                    "config/read", {"cwd": str(project), "includeLayers": False}
                )
                server = loaded["config"]["mcp_servers"]["parent_only"]
                assert server["enabled"] is True
                assert server["enabled_in_subagents"] is False
                record["project_enable_preserves_global_opt_out"] = True
                started = call("thread/start", {"cwd": str(project), "ephemeral": True})
                arguments = {
                    "server": "parent_only",
                    "threadId": started["thread"]["id"],
                    "tool": "whoami",
                    "arguments": {},
                }
                before = identity(call("mcpServer/tool/call", arguments))
                assert before and before["identity"], "Native fixture identity missing"
                config_path.write_text(
                    config.replace(
                        "enabled_in_subagents = false", "enabled_in_subagents = true"
                    ),
                    encoding="utf-8",
                )
                call(
                    "config/mcpServer/reload", None
                )  # This process owns only the private fixture.
                after = identity(call("mcpServer/tool/call", arguments))
                assert after == before, (before, after)
                record.update(status="PASS", parent_before=before, parent_after=after)
            except (
                AssertionError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                queue.Empty,
            ) as error:
                record["error"] = str(error)
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    record["forced_disposable_backend_cleanup"] = True
                    process.terminate()  # Only the process created immediately above.
                    process.wait(timeout=5)
                record["backend_exit_code"] = process.returncode
                if process.returncode != 0 or record.get(
                    "forced_disposable_backend_cleanup"
                ):
                    record["status"] = "FAIL"
        record["disposable_mcp_processes_still_alive"] = []
        if events_path.exists():
            events = [json.loads(line) for line in events_path.read_text().splitlines()]
            for row in events:
                if row["kind"] != "start":
                    continue
                probe = subprocess.run(
                    [
                        str(python),
                        "-B",
                        str(fixture),
                        "assert-gone",
                        str(row["pid"]),
                        row["identity"],
                    ],
                    check=False,
                )
                if probe.returncode:
                    record["disposable_mcp_processes_still_alive"].append(row)
                    record["status"] = "FAIL"
        if record["status"] != "PASS":
            record["backend_stderr_tail"] = (root / "stderr.log").read_text()[-2000:]
    record["finished"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
