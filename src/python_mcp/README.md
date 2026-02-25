# talk-to-figma-mcp (Python)

Python port of the TypeScript MCP server and WebSocket relay.

## Setup

```bash
pip install -r src/python_mcp/requirements.txt
```

## Run the WebSocket relay

```bash
python src/python_mcp/socket_server.py
# Runs on port 3055 by default. Override with PORT env var.
```

## Run the MCP server

```bash
python src/python_mcp/server.py
# Connects to localhost:3055 by default.
# Use --server=<hostname> to connect to a remote relay (uses wss://).
```

## Claude Desktop / Cursor config

```json
{
  "mcpServers": {
    "TalkToFigma": {
      "command": "python",
      "args": ["/path-to-repo/src/python_mcp/server.py"]
    }
  }
}
```
