"""Tiny MCP stdio server used by the test suite."""

import json
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the provided text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "slow",
        "description": "Sleep, then return.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
        },
    },
]


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        request_id = message.get("id")
        if request_id is None:
            continue
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-server", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "slow":
                time.sleep(float(arguments.get("seconds", 5)))
                result = {"content": [{"type": "text", "text": "slow done"}], "isError": False}
                print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
                continue
            if name != "echo":
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": "unknown tool"},
                        }
                    ),
                    flush=True,
                )
                continue
            result = {
                "content": [{"type": "text", "text": str(arguments.get("text", ""))}],
                "isError": False,
            }
        else:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "unknown method"},
                    }
                ),
                flush=True,
            )
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)


if __name__ == "__main__":
    main()
