# Python Port Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Port the TypeScript MCP server and WebSocket relay to Python, living in `src/python_mcp/` alongside the existing TypeScript code.

**Architecture:** Skeleton-first approach — Task 1 creates the full project setup, Task 2 builds the complete infrastructure skeleton with all 40 tool stubs, Tasks 3–7 run in parallel to fill in tool groups, Task 8 integrates everything.

**Tech Stack:** Python 3.11+, `mcp` (official Python MCP SDK), `websockets` (asyncio WebSocket), stdlib `asyncio`, `uuid`, `logging`, `argparse`

---

## Context: What This Ports

Three files from TypeScript become two Python files (Figma plugin stays JS):

| TypeScript | Python |
|---|---|
| `src/talk_to_figma_mcp/server.ts` (3100 lines, 40 tools) | `src/python_mcp/server.py` |
| `src/socket.ts` (180 lines, WebSocket relay) | `src/python_mcp/socket_server.py` |
| `src/cursor_mcp_plugin/` | **unchanged** — stays JavaScript |

The 40 MCP tools are grouped for parallel implementation:
- **Group A** (9 tools): Document & Selection
- **Group B** (7 tools): Create & Modify
- **Group C** (6 tools): Style & Appearance
- **Group D** (7 tools): Layout & Positioning
- **Group E** (11 tools): Annotations, Connections & Channel

---

## Task 1: Project Setup

**Files:**
- Create: `src/python_mcp/requirements.txt`
- Create: `src/python_mcp/README.md`

**Step 1: Create the requirements file**

```
src/python_mcp/requirements.txt
```

Content:
```
mcp>=1.0.0
websockets>=12.0
```

**Step 2: Create the README**

```
src/python_mcp/README.md
```

Content:
```markdown
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
```

**Step 3: Verify the folder exists**

Run: `ls src/python_mcp/`
Expected: `requirements.txt  README.md`

**Step 4: Commit**

```bash
git add src/python_mcp/requirements.txt src/python_mcp/README.md
git commit -m "feat(python): add project setup for Python port"
```

---

## Task 2: Skeleton — Infrastructure + All Tool Stubs

**Files:**
- Create: `src/python_mcp/server.py`
- Create: `src/python_mcp/socket_server.py`

This task creates the complete skeleton. `server.py` has all infrastructure working and all 40 tools registered but returning `"NOT IMPLEMENTED"`. `socket_server.py` is fully implemented (it's small and self-contained).

### Step 1: Create `src/python_mcp/socket_server.py`

This is a complete implementation of the WebSocket relay (port of `src/socket.ts`):

```python
#!/usr/bin/env python3
"""WebSocket relay server — port of src/socket.ts.

Clients join named channels; messages broadcast to all channel members.
Run: python socket_server.py
     PORT=3055 python socket_server.py
"""

import asyncio
import json
import logging
import os
import websockets
from websockets.server import ServerConnection

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# channel_name -> set of websocket connections
channels: dict[str, set[ServerConnection]] = {}


async def handler(ws: ServerConnection) -> None:
    logger.info("New client connected")
    await ws.send(json.dumps({
        "type": "system",
        "message": "Please join a channel to start chatting",
    }))

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            if data.get("type") == "join":
                channel_name = data.get("channel", "")
                if not channel_name or not isinstance(channel_name, str):
                    await ws.send(json.dumps({"type": "error", "message": "Channel name is required"}))
                    continue

                channels.setdefault(channel_name, set()).add(ws)

                await ws.send(json.dumps({
                    "type": "system",
                    "message": f"Joined channel: {channel_name}",
                    "channel": channel_name,
                }))
                logger.info(f"Sending join confirmation id={data.get('id')}")
                await ws.send(json.dumps({
                    "type": "system",
                    "message": {"id": data.get("id"), "result": f"Connected to channel: {channel_name}"},
                    "channel": channel_name,
                }))

                for client in channels[channel_name]:
                    if client is not ws and client.open:
                        await client.send(json.dumps({
                            "type": "system",
                            "message": "A new user has joined the channel",
                            "channel": channel_name,
                        }))

            elif data.get("type") == "message":
                channel_name = data.get("channel", "")
                if not channel_name or not isinstance(channel_name, str):
                    await ws.send(json.dumps({"type": "error", "message": "Channel name is required"}))
                    continue

                channel_clients = channels.get(channel_name)
                if channel_clients is None or ws not in channel_clients:
                    await ws.send(json.dumps({"type": "error", "message": "You must join the channel first"}))
                    continue

                logger.info(f"Broadcasting message: {data.get('message')}")
                for client in list(channel_clients):
                    if client.open:
                        await client.send(json.dumps({
                            "type": "broadcast",
                            "message": data.get("message"),
                            "sender": "You" if client is ws else "User",
                            "channel": channel_name,
                        }))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        logger.info("Client disconnected")
        for clients in channels.values():
            clients.discard(ws)


async def main() -> None:
    port = int(os.environ.get("PORT", "3055"))
    async with websockets.serve(handler, "localhost", port):
        logger.info(f"WebSocket server running on port {port}")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
```

### Step 2: Create `src/python_mcp/server.py` — infrastructure + stubs

This file has all infrastructure working. Each tool stub returns `"NOT IMPLEMENTED"`.

```python
#!/usr/bin/env python3
"""MCP server for Figma — Python port of src/talk_to_figma_mcp/server.ts.

Run: python server.py
     python server.py --server=myhost.example.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any

import websockets
import websockets.exceptions
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Logging — always to stderr, never stdout (would corrupt MCP stdio transport)
# ---------------------------------------------------------------------------
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger = logging.getLogger("figma_mcp")
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument("--server", default="localhost", help="Relay server hostname")
args, _ = parser.parse_known_args()

SERVER_HOST: str = args.server
WS_URL: str = f"ws://{SERVER_HOST}" if SERVER_HOST == "localhost" else f"wss://{SERVER_HOST}"
WS_PORT: int = 3055

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
ws_conn: websockets.WebSocketClientProtocol | None = None
pending_requests: dict[str, asyncio.Future] = {}
current_channel: str | None = None

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def rgba_to_hex(color: Any) -> str:
    """Convert an RGBA dict {r,g,b,a} with 0-1 floats to a CSS hex string.
    If color is already a hex string, return it unchanged."""
    if isinstance(color, str) and color.startswith("#"):
        return color
    r = round(color["r"] * 255)
    g = round(color["g"] * 255)
    b = round(color["b"] * 255)
    a = round(color["a"] * 255)
    hex_rgb = f"#{r:02x}{g:02x}{b:02x}"
    return hex_rgb if a == 255 else hex_rgb + f"{a:02x}"


def filter_figma_node(node: Any) -> Any | None:
    """Strip VECTOR nodes and clean up response data before returning to AI.
    Port of filterFigmaNode() in server.ts."""
    if not node or not isinstance(node, dict):
        return node
    if node.get("type") == "VECTOR":
        return None

    filtered: dict = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
    }

    if node.get("fills"):
        def process_fill(fill: dict) -> dict:
            f = {k: v for k, v in fill.items() if k not in ("boundVariables", "imageRef")}
            if "gradientStops" in f:
                stops = []
                for stop in f["gradientStops"]:
                    s = {k: v for k, v in stop.items() if k != "boundVariables"}
                    if "color" in s:
                        s["color"] = rgba_to_hex(s["color"])
                    stops.append(s)
                f["gradientStops"] = stops
            if "color" in f:
                f["color"] = rgba_to_hex(f["color"])
            return f
        filtered["fills"] = [process_fill(f) for f in node["fills"]]

    if node.get("strokes"):
        def process_stroke(stroke: dict) -> dict:
            s = {k: v for k, v in stroke.items() if k != "boundVariables"}
            if "color" in s:
                s["color"] = rgba_to_hex(s["color"])
            return s
        filtered["strokes"] = [process_stroke(s) for s in node["strokes"]]

    for key in ("cornerRadius", "absoluteBoundingBox", "characters"):
        if key in node:
            filtered[key] = node[key]

    if "style" in node:
        style = node["style"]
        filtered["style"] = {
            k: style.get(k)
            for k in ("fontFamily", "fontStyle", "fontWeight", "fontSize",
                       "textAlignHorizontal", "letterSpacing", "lineHeightPx")
        }

    if "children" in node:
        children = [filter_figma_node(c) for c in node["children"]]
        filtered["children"] = [c for c in children if c is not None]

    return filtered


def process_figma_node_response(result: Any) -> Any:
    """Log node details for debugging. Port of processFigmaNodeResponse()."""
    if result and isinstance(result, dict) and "id" in result:
        logger.info(f"Processed Figma node: {result.get('name', 'Unknown')} (ID: {result['id']})")
        if "x" in result and "y" in result:
            logger.debug(f"Node position: ({result['x']}, {result['y']})")
        if "width" in result and "height" in result:
            logger.debug(f"Node dimensions: {result['width']}×{result['height']}")
    return result

# ---------------------------------------------------------------------------
# WebSocket connection management
# ---------------------------------------------------------------------------

async def connect_to_figma(port: int = WS_PORT) -> None:
    """Connect (or reconnect) to the relay server. Port of connectToFigma()."""
    global ws_conn, current_channel
    url = f"{WS_URL}:{port}"
    logger.info(f"Connecting to Figma socket server at {url}...")
    try:
        ws_conn = await websockets.connect(url)
        current_channel = None
        logger.info("Connected to Figma socket server")
        asyncio.create_task(_listen())
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        ws_conn = None


async def _listen() -> None:
    """Background task that reads incoming WebSocket messages and resolves futures."""
    global ws_conn, current_channel
    try:
        async for raw in ws_conn:
            try:
                json_data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Progress updates — reset timeout, log, keep waiting
            if json_data.get("type") == "progress_update":
                request_id = json_data.get("id", "")
                progress_data = (json_data.get("message") or {}).get("data", {})
                if request_id and request_id in pending_requests:
                    pct = progress_data.get("progress", 0)
                    msg = progress_data.get("message", "")
                    cmd_type = progress_data.get("commandType", "")
                    logger.info(f"Progress update for {cmd_type}: {pct}% - {msg}")
                continue

            # Regular response
            my_response = json_data.get("message", {})
            logger.debug(f"Received message: {json.dumps(my_response)}")

            resp_id = my_response.get("id") if isinstance(my_response, dict) else None
            if resp_id and resp_id in pending_requests and my_response.get("result"):
                future = pending_requests.pop(resp_id)
                if not future.done():
                    if my_response.get("error"):
                        future.set_exception(Exception(my_response["error"]))
                    else:
                        future.set_result(my_response["result"])
            else:
                logger.info(f"Received broadcast message: {json.dumps(my_response)}")

    except websockets.exceptions.ConnectionClosed:
        logger.info("Disconnected from Figma socket server")
    except Exception as e:
        logger.error(f"Listener error: {e}")
    finally:
        ws_conn = None
        current_channel = None
        for fut in pending_requests.values():
            if not fut.done():
                fut.set_exception(Exception("WebSocket connection closed"))
        pending_requests.clear()


async def join_channel(channel_name: str) -> None:
    """Join a relay channel. Must be called before any other command."""
    global current_channel
    if ws_conn is None or not ws_conn.open:
        await connect_to_figma()
    if ws_conn is None:
        raise RuntimeError("Not connected to Figma relay")

    request_id = str(uuid.uuid4())
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_requests[request_id] = future

    await ws_conn.send(json.dumps({
        "id": request_id,
        "type": "join",
        "channel": channel_name,
    }))

    try:
        await asyncio.wait_for(future, timeout=30.0)
        current_channel = channel_name
        logger.info(f"Joined channel: {channel_name}")
    except asyncio.TimeoutError:
        pending_requests.pop(request_id, None)
        raise TimeoutError("Joining channel timed out")


async def send_command(command: str, params: dict | None = None, timeout_ms: int = 30000) -> Any:
    """Send a command to Figma via the relay and await the response.
    Port of sendCommandToFigma() in server.ts."""
    global ws_conn, current_channel

    if ws_conn is None or not ws_conn.open:
        await connect_to_figma()
    if ws_conn is None or not ws_conn.open:
        raise RuntimeError("Not connected to Figma. Start the relay server and reconnect.")

    if command != "join" and current_channel is None:
        raise RuntimeError("Must join a channel before sending commands")

    request_id = str(uuid.uuid4())
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_requests[request_id] = future

    payload = params or {}
    payload["commandId"] = request_id

    request = {
        "id": request_id,
        "type": "join" if command == "join" else "message",
        **({"channel": payload.get("channel")} if command == "join" else {"channel": current_channel}),
        "message": {
            "id": request_id,
            "command": command,
            "params": payload,
        },
    }

    logger.info(f"Sending command to Figma: {command}")
    logger.debug(f"Request details: {json.dumps(request)}")
    await ws_conn.send(json.dumps(request))

    try:
        result = await asyncio.wait_for(future, timeout=timeout_ms / 1000)
        return result
    except asyncio.TimeoutError:
        pending_requests.pop(request_id, None)
        raise TimeoutError(f"Request to Figma timed out after {timeout_ms // 1000}s")

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = Server("TalkToFigmaMCP")

# ---- Helpers for building tool responses ----

def ok(result: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(result))]

def err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=msg)]

def _stub(name: str) -> list[TextContent]:
    return err(f"Tool '{name}' not yet implemented")

# ---------------------------------------------------------------------------
# Tool definitions — Group A: Document & Selection (stubs)
# ---------------------------------------------------------------------------

@mcp.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="get_document_info", description="Get detailed information about the current Figma document", inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_selection", description="Get information about the current selection in Figma", inputSchema={"type": "object", "properties": {}}),
        Tool(name="read_my_design", description="Get detailed information about the current selection in Figma, including all node details", inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_node_info", description="Get detailed information about a specific node in Figma", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}}, "required": ["nodeId"]}),
        Tool(name="get_nodes_info", description="Get detailed information about multiple nodes in Figma", inputSchema={"type": "object", "properties": {"nodeIds": {"type": "array", "items": {"type": "string"}}}, "required": ["nodeIds"]}),
        Tool(name="get_styles", description="Get all styles from the current Figma document", inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_local_components", description="Get all local components from the current Figma document", inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_annotations", description="Get annotations from the current Figma document", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "includeCategories": {"type": "boolean"}}}),
        Tool(name="get_reactions", description="Get reactions/interactions for nodes", inputSchema={"type": "object", "properties": {"nodeIds": {"type": "array", "items": {"type": "string"}}}, "required": ["nodeIds"]}),
        # Group B: Create & Modify
        Tool(name="create_rectangle", description="Create a new rectangle in Figma", inputSchema={"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "width": {"type": "number"}, "height": {"type": "number"}, "name": {"type": "string"}, "parentId": {"type": "string"}}}),
        Tool(name="create_frame", description="Create a new frame in Figma", inputSchema={"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "width": {"type": "number"}, "height": {"type": "number"}, "name": {"type": "string"}, "parentId": {"type": "string"}}}),
        Tool(name="create_text", description="Create a new text element in Figma", inputSchema={"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "text": {"type": "string"}, "fontSize": {"type": "number"}, "fontWeight": {"type": "number"}, "name": {"type": "string"}, "parentId": {"type": "string"}}}),
        Tool(name="create_component_instance", description="Create an instance of a Figma component", inputSchema={"type": "object", "properties": {"componentId": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}, "parentId": {"type": "string"}}, "required": ["componentId"]}),
        Tool(name="clone_node", description="Clone an existing node in Figma", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["nodeId"]}),
        Tool(name="delete_node", description="Delete a node from Figma", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}}, "required": ["nodeId"]}),
        Tool(name="delete_multiple_nodes", description="Delete multiple nodes from Figma", inputSchema={"type": "object", "properties": {"nodeIds": {"type": "array", "items": {"type": "string"}}}, "required": ["nodeIds"]}),
        # Group C: Style & Appearance
        Tool(name="set_fill_color", description="Set the fill color of a node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "r": {"type": "number"}, "g": {"type": "number"}, "b": {"type": "number"}, "a": {"type": "number"}}, "required": ["nodeId", "r", "g", "b"]}),
        Tool(name="set_stroke_color", description="Set the stroke color of a node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "r": {"type": "number"}, "g": {"type": "number"}, "b": {"type": "number"}, "a": {"type": "number"}, "weight": {"type": "number"}}, "required": ["nodeId", "r", "g", "b"]}),
        Tool(name="set_corner_radius", description="Set the corner radius of a node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "radius": {"type": "number"}, "topLeft": {"type": "number"}, "topRight": {"type": "number"}, "bottomLeft": {"type": "number"}, "bottomRight": {"type": "number"}}, "required": ["nodeId", "radius"]}),
        Tool(name="set_text_content", description="Set the text content of a text node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "text": {"type": "string"}}, "required": ["nodeId", "text"]}),
        Tool(name="set_multiple_text_contents", description="Set text content for multiple text nodes at once", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "text": {"type": "array", "items": {"type": "object", "properties": {"nodeId": {"type": "string"}, "text": {"type": "string"}}}}}, "required": ["nodeId", "text"]}),
        Tool(name="export_node_as_image", description="Export a node as an image", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "format": {"type": "string"}, "scale": {"type": "number"}}, "required": ["nodeId"]}),
        # Group D: Layout & Positioning
        Tool(name="move_node", description="Move a node to a new position", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["nodeId", "x", "y"]}),
        Tool(name="resize_node", description="Resize a node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "width": {"type": "number"}, "height": {"type": "number"}}, "required": ["nodeId", "width", "height"]}),
        Tool(name="set_layout_mode", description="Set the layout mode of a frame (horizontal/vertical auto layout)", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "layoutMode": {"type": "string"}}, "required": ["nodeId", "layoutMode"]}),
        Tool(name="set_padding", description="Set padding on an auto layout frame", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "top": {"type": "number"}, "right": {"type": "number"}, "bottom": {"type": "number"}, "left": {"type": "number"}}, "required": ["nodeId"]}),
        Tool(name="set_axis_align", description="Set alignment on primary and counter axis for auto layout", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "primaryAxisAlignItems": {"type": "string"}, "counterAxisAlignItems": {"type": "string"}}, "required": ["nodeId"]}),
        Tool(name="set_layout_sizing", description="Set horizontal and vertical sizing on an auto layout child", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "horizontalSizing": {"type": "string"}, "verticalSizing": {"type": "string"}}, "required": ["nodeId"]}),
        Tool(name="set_item_spacing", description="Set item spacing in an auto layout frame", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "spacing": {"type": "number"}}, "required": ["nodeId", "spacing"]}),
        # Group E: Annotations, Connections & Channel
        Tool(name="set_annotation", description="Set an annotation on a node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "annotationId": {"type": "string"}, "labelMarkdown": {"type": "string"}, "categoryId": {"type": "string"}, "properties": {"type": "array"}}, "required": ["nodeId", "labelMarkdown"]}),
        Tool(name="set_multiple_annotations", description="Set annotations on multiple nodes at once", inputSchema={"type": "object", "properties": {"annotations": {"type": "array"}}, "required": ["annotations"]}),
        Tool(name="scan_text_nodes", description="Scan text nodes within a node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "useChunking": {"type": "boolean"}, "chunkSize": {"type": "number"}}, "required": ["nodeId"]}),
        Tool(name="scan_nodes_by_types", description="Scan nodes by their types within a node", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}, "types": {"type": "array", "items": {"type": "string"}}}, "required": ["nodeId", "types"]}),
        Tool(name="get_instance_overrides", description="Get overrides from a component instance", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}}, "required": ["nodeId"]}),
        Tool(name="set_instance_overrides", description="Apply instance overrides to component instances", inputSchema={"type": "object", "properties": {"sourceNodeId": {"type": "string"}, "targetNodeIds": {"type": "array", "items": {"type": "string"}}}, "required": ["sourceNodeId", "targetNodeIds"]}),
        Tool(name="set_default_connector", description="Set the default connector style in FigJam", inputSchema={"type": "object", "properties": {"connectorId": {"type": "string"}}}),
        Tool(name="create_connections", description="Create connections between nodes in FigJam", inputSchema={"type": "object", "properties": {"connections": {"type": "array"}}, "required": ["connections"]}),
        Tool(name="set_focus", description="Set focus on a node in Figma", inputSchema={"type": "object", "properties": {"nodeId": {"type": "string"}}, "required": ["nodeId"]}),
        Tool(name="set_selections", description="Set the current selection in Figma", inputSchema={"type": "object", "properties": {"nodeIds": {"type": "array", "items": {"type": "string"}}}, "required": ["nodeIds"]}),
        Tool(name="join_channel", description="Join a specific channel to communicate with Figma", inputSchema={"type": "object", "properties": {"channel": {"type": "string", "default": ""}}}),
    ]


@mcp.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route all tool calls. Each group will fill in their section."""
    # Group A: Document & Selection
    if name == "get_document_info":
        return _stub(name)
    if name == "get_selection":
        return _stub(name)
    if name == "read_my_design":
        return _stub(name)
    if name == "get_node_info":
        return _stub(name)
    if name == "get_nodes_info":
        return _stub(name)
    if name == "get_styles":
        return _stub(name)
    if name == "get_local_components":
        return _stub(name)
    if name == "get_annotations":
        return _stub(name)
    if name == "get_reactions":
        return _stub(name)
    # Group B: Create & Modify
    if name == "create_rectangle":
        return _stub(name)
    if name == "create_frame":
        return _stub(name)
    if name == "create_text":
        return _stub(name)
    if name == "create_component_instance":
        return _stub(name)
    if name == "clone_node":
        return _stub(name)
    if name == "delete_node":
        return _stub(name)
    if name == "delete_multiple_nodes":
        return _stub(name)
    # Group C: Style & Appearance
    if name == "set_fill_color":
        return _stub(name)
    if name == "set_stroke_color":
        return _stub(name)
    if name == "set_corner_radius":
        return _stub(name)
    if name == "set_text_content":
        return _stub(name)
    if name == "set_multiple_text_contents":
        return _stub(name)
    if name == "export_node_as_image":
        return _stub(name)
    # Group D: Layout & Positioning
    if name == "move_node":
        return _stub(name)
    if name == "resize_node":
        return _stub(name)
    if name == "set_layout_mode":
        return _stub(name)
    if name == "set_padding":
        return _stub(name)
    if name == "set_axis_align":
        return _stub(name)
    if name == "set_layout_sizing":
        return _stub(name)
    if name == "set_item_spacing":
        return _stub(name)
    # Group E: Annotations, Connections & Channel
    if name == "set_annotation":
        return _stub(name)
    if name == "set_multiple_annotations":
        return _stub(name)
    if name == "scan_text_nodes":
        return _stub(name)
    if name == "scan_nodes_by_types":
        return _stub(name)
    if name == "get_instance_overrides":
        return _stub(name)
    if name == "set_instance_overrides":
        return _stub(name)
    if name == "set_default_connector":
        return _stub(name)
    if name == "create_connections":
        return _stub(name)
    if name == "set_focus":
        return _stub(name)
    if name == "set_selections":
        return _stub(name)
    if name == "join_channel":
        return _stub(name)

    return err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    try:
        await connect_to_figma()
    except Exception as e:
        logger.warning(f"Could not connect to Figma initially: {e}")
        logger.warning("Will try to connect when the first command is sent")

    async with stdio_server() as (read_stream, write_stream):
        logger.info("FigmaMCP server running on stdio")
        await mcp.run(read_stream, write_stream, mcp.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
```

### Step 3: Verify the skeleton starts without errors

Run:
```bash
cd /path-to-repo
python src/python_mcp/server.py &
sleep 2
kill %1
```

Expected: No import errors, prints `[WARNING] Could not connect to Figma initially` (relay isn't running), then exits cleanly.

### Step 4: Verify socket server starts

Run:
```bash
python src/python_mcp/socket_server.py &
sleep 1
kill %1
```

Expected: Prints `[INFO] WebSocket server running on port 3055`

### Step 5: Commit

```bash
git add src/python_mcp/server.py src/python_mcp/socket_server.py
git commit -m "feat(python): add skeleton server and relay with all 40 tool stubs"
```

---

## Task 3: Fill in Group A — Document & Selection

> **Run in parallel with Tasks 4, 5, 6, 7 after Task 2 is complete.**

**Files:**
- Modify: `src/python_mcp/server.py` — replace stubs for Group A tools only

**Reference:** `src/talk_to_figma_mcp/server.ts` lines 89–399 (get_document_info, get_selection, read_my_design, get_node_info, get_nodes_info), 936–1084 (get_styles, get_local_components, get_annotations), 2494+ (get_reactions)

**Tools:** `get_document_info`, `get_selection`, `read_my_design`, `get_node_info`, `get_nodes_info`, `get_styles`, `get_local_components`, `get_annotations`, `get_reactions`

**Step 1: Replace each stub in `call_tool()` with the real implementation**

Pattern for all simple tools (no params):
```python
if name == "get_document_info":
    try:
        result = await send_command("get_document_info")
        return ok(result)
    except Exception as e:
        return err(f"Error getting document info: {e}")
```

Pattern for tools with params:
```python
if name == "get_node_info":
    try:
        result = await send_command("get_node_info", {"nodeId": arguments["nodeId"]})
        return ok(filter_figma_node(result))
    except Exception as e:
        return err(f"Error getting node info: {e}")
```

For `get_nodes_info` — read TS lines 314–350 for the exact params structure.
For `get_annotations` — params: `nodeId` (optional), `includeCategories` (optional bool).
For `get_reactions` — params: `nodeIds` (list of strings).

**Step 2: Verify by running**

```bash
python src/python_mcp/server.py &
sleep 1
kill %1
```

Expected: Starts without errors.

**Step 3: Commit**

```bash
git add src/python_mcp/server.py
git commit -m "feat(python): implement Group A — Document & Selection tools"
```

---

## Task 4: Fill in Group B — Create & Modify

> **Run in parallel with Tasks 3, 5, 6, 7 after Task 2 is complete.**

**Files:**
- Modify: `src/python_mcp/server.py` — replace stubs for Group B tools only

**Reference:** `src/talk_to_figma_mcp/server.ts` lines 528–822 (create_rectangle, create_frame, create_text), 1274+ (create_component_instance), 855–965 (clone_node, delete_node, delete_multiple_nodes)

**Tools:** `create_rectangle`, `create_frame`, `create_text`, `create_component_instance`, `clone_node`, `delete_node`, `delete_multiple_nodes`

**Step 1: Replace stubs in `call_tool()` for Group B**

Example pattern:
```python
if name == "create_rectangle":
    try:
        params = {
            "x": arguments.get("x", 0),
            "y": arguments.get("y", 0),
            "width": arguments.get("width", 100),
            "height": arguments.get("height", 100),
            "name": arguments.get("name", "Rectangle"),
        }
        if "parentId" in arguments:
            params["parentId"] = arguments["parentId"]
        result = await send_command("create_rectangle", params)
        return ok(result)
    except Exception as e:
        return err(f"Error creating rectangle: {e}")
```

Read the TS source for each tool's exact param names and defaults before implementing.

**Step 2: Verify**

```bash
python src/python_mcp/server.py &
sleep 1
kill %1
```

**Step 3: Commit**

```bash
git add src/python_mcp/server.py
git commit -m "feat(python): implement Group B — Create & Modify tools"
```

---

## Task 5: Fill in Group C — Style & Appearance

> **Run in parallel with Tasks 3, 4, 6, 7 after Task 2 is complete.**

**Files:**
- Modify: `src/python_mcp/server.py` — replace stubs for Group C tools only

**Reference:** `src/talk_to_figma_mcp/server.ts` lines 640–854 (set_fill_color, set_stroke_color, move_node, resize_node — check exact lines), 966–1030 (set_corner_radius, set_text_content), 1774+ (set_multiple_text_contents), 823+ (export_node_as_image)

**Tools:** `set_fill_color`, `set_stroke_color`, `set_corner_radius`, `set_text_content`, `set_multiple_text_contents`, `export_node_as_image`

**Step 1: Replace stubs in `call_tool()` for Group C**

Example:
```python
if name == "set_fill_color":
    try:
        params = {
            "nodeId": arguments["nodeId"],
            "r": arguments["r"],
            "g": arguments["g"],
            "b": arguments["b"],
            "a": arguments.get("a", 1),
        }
        result = await send_command("set_fill_color", params)
        return ok(result)
    except Exception as e:
        return err(f"Error setting fill color: {e}")
```

For `set_multiple_text_contents` — read TS lines 1774+ carefully; it uses chunking logic.
For `export_node_as_image` — params: `nodeId`, `format` (default "PNG"), `scale` (default 1).

**Step 2: Verify**

```bash
python src/python_mcp/server.py &
sleep 1
kill %1
```

**Step 3: Commit**

```bash
git add src/python_mcp/server.py
git commit -m "feat(python): implement Group C — Style & Appearance tools"
```

---

## Task 6: Fill in Group D — Layout & Positioning

> **Run in parallel with Tasks 3, 4, 5, 7 after Task 2 is complete.**

**Files:**
- Modify: `src/python_mcp/server.py` — replace stubs for Group D tools only

**Reference:** `src/talk_to_figma_mcp/server.ts` lines 718–791 (move_node, resize_node), 2096–2412 (set_layout_mode, set_padding, set_axis_align, set_layout_sizing, set_item_spacing)

**Tools:** `move_node`, `resize_node`, `set_layout_mode`, `set_padding`, `set_axis_align`, `set_layout_sizing`, `set_item_spacing`

**Step 1: Replace stubs in `call_tool()` for Group D**

Example:
```python
if name == "move_node":
    try:
        result = await send_command("move_node", {
            "nodeId": arguments["nodeId"],
            "x": arguments["x"],
            "y": arguments["y"],
        })
        return ok(result)
    except Exception as e:
        return err(f"Error moving node: {e}")

if name == "set_layout_mode":
    try:
        result = await send_command("set_layout_mode", {
            "nodeId": arguments["nodeId"],
            "layoutMode": arguments["layoutMode"],  # "HORIZONTAL" | "VERTICAL" | "NONE"
        })
        return ok(result)
    except Exception as e:
        return err(f"Error setting layout mode: {e}")
```

Read TS source for set_padding (top/right/bottom/left), set_axis_align (primaryAxisAlignItems, counterAxisAlignItems), set_layout_sizing (horizontalSizing, verticalSizing).

**Step 2: Verify**

```bash
python src/python_mcp/server.py &
sleep 1
kill %1
```

**Step 3: Commit**

```bash
git add src/python_mcp/server.py
git commit -m "feat(python): implement Group D — Layout & Positioning tools"
```

---

## Task 7: Fill in Group E — Annotations, Connections & Channel

> **Run in parallel with Tasks 3, 4, 5, 6 after Task 2 is complete.**

**Files:**
- Modify: `src/python_mcp/server.py` — replace stubs for Group E tools only

**Reference:** `src/talk_to_figma_mcp/server.ts` lines 1085–1237 (set_annotation, set_multiple_annotations), 1481–1558 (scan_text_nodes), 2188+ (scan_nodes_by_types), 1238–1480 (get_instance_overrides, set_instance_overrides), 2339–2493 (set_default_connector, create_connections), 2494+ (set_focus, set_selections), 3033+ (join_channel)

**Tools:** `set_annotation`, `set_multiple_annotations`, `scan_text_nodes`, `scan_nodes_by_types`, `get_instance_overrides`, `set_instance_overrides`, `set_default_connector`, `create_connections`, `set_focus`, `set_selections`, `join_channel`

**Step 1: Implement `join_channel` tool first (most important)**

```python
if name == "join_channel":
    try:
        channel = arguments.get("channel", "")
        if not channel:
            return err("Please provide a channel name to join")
        await join_channel(channel)
        return ok(f"Successfully joined channel: {channel}")
    except Exception as e:
        return err(f"Error joining channel: {e}")
```

**Step 2: Implement remaining Group E stubs**

For `scan_text_nodes` — params: `nodeId`, `useChunking` (bool), `chunkSize` (int). Read TS lines 1481+ for chunking behavior.
For `set_multiple_annotations` — read TS lines 1085+ for the full params shape.
For `create_connections` — params: `connections` list with `startNodeId`, `endNodeId`, optional `text`.

**Step 3: Verify**

```bash
python src/python_mcp/server.py &
sleep 1
kill %1
```

**Step 4: Commit**

```bash
git add src/python_mcp/server.py
git commit -m "feat(python): implement Group E — Annotations, Connections & Channel tools"
```

---

## Task 8: Integration & End-to-End Verification

**Files:**
- Read: `src/python_mcp/server.py` (verify all stubs replaced)
- Read: `src/python_mcp/socket_server.py` (already complete)

**Step 1: Confirm no stubs remain**

Run:
```bash
grep "_stub(" src/python_mcp/server.py
```

Expected: No output. If any stubs remain, implement them before continuing.

**Step 2: Start relay and test WebSocket connection**

```bash
# Terminal 1
python src/python_mcp/socket_server.py

# Terminal 2
python -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://localhost:3055') as ws:
        await ws.recv()  # welcome message
        await ws.send(json.dumps({'type': 'join', 'channel': 'test', 'id': 'abc'}))
        msg1 = await ws.recv()
        msg2 = await ws.recv()
        print('msg1:', msg1)
        print('msg2:', msg2)

asyncio.run(test())
"
```

Expected: Prints two JSON messages confirming channel join.

**Step 3: Test MCP server startup**

```bash
# With relay running in background:
python src/python_mcp/socket_server.py &
sleep 0.5
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | python src/python_mcp/server.py
```

Expected: Returns a JSON `initialize` response listing the server name `TalkToFigmaMCP`.

**Step 4: Final commit**

```bash
git add src/python_mcp/
git commit -m "feat(python): complete Python port — all 40 tools implemented"
```

---

## Quick Reference

**Tool name → TypeScript line numbers in server.ts:**

| Tool | Approx. TS line |
|---|---|
| get_document_info | 89 |
| get_selection | 119 |
| read_my_design | 149 |
| get_node_info | 179 |
| get_nodes_info | 314 |
| create_rectangle | 352 |
| create_frame | 399 |
| create_text | 528 |
| set_fill_color | 599 |
| set_stroke_color | 640 |
| move_node | 683 |
| clone_node | 718 |
| resize_node | 752 |
| delete_node | 791 |
| delete_multiple_nodes | 823 |
| export_node_as_image | 855 |
| set_text_content | 899 |
| get_styles | 936 |
| get_local_components | 966 |
| get_annotations | 996 |
| set_annotation | 1085 |
| set_multiple_annotations | 1198 |
| create_component_instance | 1237 |
| get_instance_overrides | 1274 |
| set_instance_overrides | 1324 |
| set_corner_radius | 1481 |
| scan_text_nodes | 1558 |
| scan_nodes_by_types | 1774 |
| set_multiple_text_contents | 2096 |
| set_layout_mode | 2134 |
| set_padding | 2188 |
| set_axis_align | 2242 |
| set_layout_sizing | 2296 |
| set_item_spacing | 2339 |
| get_reactions | 2379 |
| set_default_connector | 2413 |
| create_connections | 2462 |
| set_focus | 2494 |
| set_selections | 3033 (near end) |
| join_channel | 3033 |
