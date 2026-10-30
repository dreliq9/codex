"""Disposable stdio fixture. No application configuration, auth, network, or hardware."""
import atexit
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time


def identity(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [ctypes.c_uint64() for _ in range(4)]
            status = ctypes.c_uint32()
            if not kernel.GetProcessTimes(handle, *[ctypes.byref(v) for v in times]):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
                raise ctypes.WinError(ctypes.get_last_error())
            return str(times[0].value) if status.value == 259 else None
        finally:
            kernel.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


if sys.argv[1] == "assert-gone":
    raise SystemExit(1 if identity(int(sys.argv[2])) == sys.argv[3] else 0)

log, owner = Path(sys.argv[1]), sys.argv[2]
instance = {"pid": os.getpid(), "identity": identity(os.getpid()), "owner": owner}


def event(kind):
    try:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": kind, **instance}) + "\n")
    except FileNotFoundError:
        # A failed test may have already removed its private temporary directory.
        pass


event("start")
atexit.register(event, "exit")


def expire():
    time.sleep(180)
    try:
        event("lease-expired")
    finally:
        os._exit(0)


threading.Thread(target=expire, daemon=True).start()
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {"protocolVersion": request["params"]["protocolVersion"],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "worker-scope-fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "whoami", "description": "Return fixture identity",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps(instance)}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
