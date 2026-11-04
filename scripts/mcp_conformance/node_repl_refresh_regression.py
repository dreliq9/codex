"""Replay the desktop's exact managed Node transport payload in a private backend."""

import argparse
import copy
import datetime as dt
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--payload", type=Path, help="Optional synthetic managed transport payload"
    )
    options = parser.parse_args()
    value = (
        json.loads(options.payload.read_text())
        if options.payload
        else {
            "command": "C:/new-runtime/node_repl.exe",
            "args": [],
            "env": {"NODE_REPL_NODE_PATH": "C:/new-runtime/node.exe"},
            "env_vars": [],
            "startup_timeout_sec": 120,
        }
    )
    record = {
        "started": dt.datetime.now(dt.timezone.utc).isoformat(),
        "binary": str(options.binary),
        "live_backend_connection": False,
        "model_turns": 0,
        "mcp_processes_started": False,
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="node-policy-rpc-") as directory:
        root = Path(directory)
        home = root / "home"
        project = root / "project"
        home.mkdir()
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        prefix = """model = "no-inference"
model_provider = "isolated"
approval_policy = "never"
sandbox_mode = "read-only"
[features]
apps = false
plugins = false
[analytics]
enabled = false
[model_providers.isolated]
name = "Private no-inference test"
base_url = "http://127.0.0.1:1"
wire_api = "responses"
requires_openai_auth = false
"""
        config = home / "config.toml"

        def seed(policy=False, name="node_repl"):
            flag = (
                ""
                if policy is None
                else "enabled_in_subagents = " + str(policy).lower() + "\n"
            )
            config.write_text(
                prefix
                + f"\n[mcp_servers.{name}]\n"
                + 'command = "old"\n'
                + flag
                + f'[mcp_servers.{name}.env]\nSTALE = "old"\n',
                encoding="utf-8",
            )

        seed()
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "COMSPEC",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env["CODEX_HOME"] = str(home)
        messages = queue.Queue()
        counter = 0
        with (root / "backend-stderr.log").open("w") as errors:
            process = subprocess.Popen(
                [str(options.binary), "app-server"],
                cwd=project,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
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

            def send(obj):
                process.stdin.write(json.dumps(obj) + "\n")
                process.stdin.flush()

            def call(method, params):
                nonlocal counter
                counter += 1
                identifier = counter
                send({"id": identifier, "method": method, "params": params})
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    response = messages.get(
                        timeout=max(0.1, deadline - time.monotonic())
                    )
                    if response.get("closed"):
                        raise RuntimeError("Private backend closed")
                    if response.get("id") == identifier:
                        return response
                raise TimeoutError(method)

            def edit(path, new_value):
                return {"keyPath": path, "value": new_value, "mergeStrategy": "replace"}

            def case(
                label,
                policy,
                edits,
                expected,
                name="node_repl",
                invalid=False,
                deleted=False,
            ):
                seed(policy, name)
                before = config.read_bytes()
                response = call(
                    "config/batchWrite",
                    {
                        "edits": edits,
                        "filePath": None,
                        "expectedVersion": None,
                        "reloadUserConfig": False,
                    },
                )
                if invalid:
                    assert "error" in response, label
                    assert config.read_bytes() == before, label
                    record["cases"].append(
                        {"case": label, "rejected_without_change": True}
                    )
                    return
                assert "error" not in response, (label, response.get("error"))
                saved = tomllib.loads(config.read_text())
                server = saved.get("mcp_servers", {}).get(name)
                if deleted:
                    assert server is None, label
                    record["cases"].append({"case": label, "deleted": True})
                    return
                actual = server.get("enabled_in_subagents")
                assert actual is expected, (label, actual, expected)
                assert server["command"] == edits[-1]["value"]["command"], label
                assert "STALE" not in server.get("env", {}), label
                record["cases"].append(
                    {
                        "case": label,
                        "policy": actual,
                        "transport_replaced_without_stale_env": True,
                    }
                )

            try:
                response = call(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "node-policy-regression",
                            "version": "1",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                )
                assert "error" not in response
                send({"method": "initialized"})
                path = "mcp_servers.node_repl"
                case(
                    "desktop_refresh_retains_false",
                    False,
                    [edit(path, value)],
                    None if options.baseline else False,
                )
                case(
                    "desktop_refresh_retains_true",
                    True,
                    [edit(path, value)],
                    None if options.baseline else True,
                )
                case("absent_policy_stays_absent", None, [edit(path, value)], None)
                for incoming, existing in [(False, True), (True, False)]:
                    explicit = copy.deepcopy(value)
                    explicit["enabled_in_subagents"] = incoming
                    case(
                        "explicit_policy_" + str(incoming),
                        existing,
                        [edit(path, explicit)],
                        incoming,
                    )
                custom = copy.deepcopy(value)
                custom["command"] = "C:/custom/python.exe"
                case(
                    "custom_launcher_replace_unchanged",
                    False,
                    [edit(path, custom)],
                    None,
                )
                custom_args = copy.deepcopy(value)
                custom_args["args"] = ["custom-mode"]
                case(
                    "custom_arguments_replace_unchanged",
                    False,
                    [edit(path, custom_args)],
                    None,
                )
                posix = copy.deepcopy(value)
                posix["command"] = "/new-runtime/node_repl"
                case(
                    "posix_native_transport_retains_policy",
                    False,
                    [edit(path, posix)],
                    None if options.baseline else False,
                )
                windows = copy.deepcopy(value)
                windows["command"] = r"C:\new-runtime\node_repl.exe"
                case(
                    "windows_native_backslashes_retain_policy",
                    False,
                    [edit(path, windows)],
                    None if options.baseline else False,
                )
                case(
                    "other_server_replace_unchanged",
                    False,
                    [edit("mcp_servers.other", value)],
                    None,
                    name="other",
                )
                case(
                    "ordered_policy_set_then_refresh",
                    False,
                    [edit(path + ".enabled_in_subagents", True), edit(path, value)],
                    None if options.baseline else True,
                )
                case(
                    "ordered_policy_clear_then_refresh",
                    False,
                    [edit(path + ".enabled_in_subagents", None), edit(path, value)],
                    None,
                )
                bad = copy.deepcopy(value)
                bad["enabled_in_subagents"] = "invalid"
                case(
                    "invalid_explicit_policy_rejected",
                    False,
                    [edit(path, bad)],
                    None,
                    invalid=True,
                )
                case(
                    "explicit_server_delete",
                    False,
                    [edit(path, None)],
                    None,
                    deleted=True,
                )
                record["status"] = (
                    "BASELINE_OVERWRITE_REPRODUCED" if options.baseline else "PASS"
                )
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()  # Only the disposable backend created above.
                    process.wait(timeout=5)
                    raise RuntimeError("Private backend did not close normally")
                record["backend_exit_code"] = process.returncode
                assert process.returncode == 0
    record["finished"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
