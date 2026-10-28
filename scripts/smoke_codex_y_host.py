#!/usr/bin/env python3
"""Exercise an installed Code Mode Host over stdio, without a model request."""

import argparse
import json
from pathlib import Path
import select
import struct
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "host",
        type=Path,
        nargs="?",
        default=Path.home()
        / ".codex-y/packages/app-server-daemon/current/bin/codex-code-mode-host",
    )
    args = parser.parse_args()
    process = subprocess.Popen(
        [str(args.host), "--listen", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        bufsize=0,
    )
    deadline = time.monotonic() + 30

    def send(message):
        data = json.dumps(message).encode()
        process.stdin.write(struct.pack("<I", len(data)) + data)
        process.stdin.flush()

    def read_exact(length):
        data = bytearray()
        while len(data) < length:
            if not select.select(
                [process.stdout], [], [], max(0, deadline - time.monotonic())
            )[0]:
                raise TimeoutError("Code Mode Host response timed out")
            chunk = process.stdout.read(length - len(data))
            if not chunk:
                raise RuntimeError("Code Mode Host closed stdout")
            data.extend(chunk)
        return bytes(data)

    def receive():
        length = struct.unpack("<I", read_exact(4))[0]
        if length > 64 * 1024 * 1024:
            raise RuntimeError("Invalid host frame length")
        return json.loads(read_exact(length))

    try:
        send(
            {
                "type": "connection/hello",
                "supportedVersions": [1],
                "requiredCapabilities": [],
                "optionalCapabilities": [],
            }
        )
        assert receive()["type"] == "connection/ready"
        send(
            {
                "type": "operation/request",
                "id": 1,
                "request": {"method": "session/open", "sessionId": "smoke"},
            }
        )
        assert receive()["result"]["value"]["type"] == "session/ready"
        send(
            {
                "type": "operation/request",
                "id": 2,
                "request": {
                    "method": "session/execute",
                    "sessionId": "smoke",
                    "request": {
                        "tool_call_id": "smoke",
                        "enabled_tools": [
                            {
                                "name": "smoke_shell",
                                "tool_name": {"name": "smoke_shell", "namespace": None},
                                "description": "Run the fixed smoke-test command",
                                "kind": "function",
                                "input_schema": {"type": "object", "properties": {}},
                                "output_schema": None,
                            }
                        ],
                        "source": "text(await tools.smoke_shell({}));",
                        "yield_time_ms": 10000,
                        "max_output_tokens": 100,
                    },
                },
            }
        )
        invoked = False
        while True:
            message = receive()
            if message["type"] == "delegate/request":
                assert message["request"]["type"] == "tool/invoke"
                assert (
                    message["request"]["invocation"]["tool_name"]["name"]
                    == "smoke_shell"
                )
                # Run a fixed harmless command; never execute text received from the host.
                result = subprocess.check_output(
                    ["/bin/sh", "-c", "printf CODEX_Y_HOST_OK"],
                    text=True,
                    timeout=5,
                )
                invoked = True
                send(
                    {
                        "type": "delegate/response",
                        "id": message["id"],
                        "result": {
                            "status": "ok",
                            "value": {"type": "tool/result", "result": result},
                        },
                    }
                )
            elif message["type"] == "execute/initialResponse":
                assert message["result"]["status"] == "ok", message
                result = message["result"]["value"]["Result"]
                assert result["error_text"] is None, result
                assert invoked and "CODEX_Y_HOST_OK" in json.dumps(result), result
                break
        print(
            "PASS: installed host handshake, JavaScript execution, shell callback and result delivery"
        )
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


if __name__ == "__main__":
    main()
