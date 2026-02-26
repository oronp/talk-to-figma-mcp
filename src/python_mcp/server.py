#!/usr/bin/env python3
"""
MCP server for talk-to-figma-mcp — Python port of src/talk_to_figma_mcp/server.ts.

Infrastructure is fully implemented; individual tool handlers are stubs that
will be filled in by later tasks.

Usage:
    python server.py [--server=<hostname>]
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any, Dict, List, Optional

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Logging — ALL output goes to stderr to avoid polluting the MCP stdio transport
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("talk_to_figma_mcp")

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
_arg_parser = argparse.ArgumentParser(description="Talk-to-Figma MCP server")
_arg_parser.add_argument(
    "--server",
    default="localhost",
    help="Hostname of the WebSocket relay server (default: localhost)",
)
# parse_known_args so that the MCP SDK's own argv parsing doesn't cause errors
_args, _unknown = _arg_parser.parse_known_args()

SERVER_URL: str = _args.server
WS_BASE: str = f"ws://{SERVER_URL}" if SERVER_URL == "localhost" else f"wss://{SERVER_URL}"

# ---------------------------------------------------------------------------
# Global WebSocket state
# ---------------------------------------------------------------------------
ws_conn: Optional[Any] = None  # websockets.ClientConnection once connected
pending_requests: Dict[str, asyncio.Future] = {}
current_channel: Optional[str] = None
_listen_task: Optional[asyncio.Task] = None

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------
server = Server("TalkToFigmaMCP")

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def rgba_to_hex(color: Any) -> str:
    """Convert an RGBA dict (0-1 float values) to a CSS hex string.

    If *color* is already a hex string it is returned unchanged.
    """
    if isinstance(color, str):
        if color.startswith("#"):
            return color
        # unexpected string — return as-is
        return color

    r = round(color.get("r", 0) * 255)
    g = round(color.get("g", 0) * 255)
    b = round(color.get("b", 0) * 255)
    a = round(color.get("a", 1) * 255)

    hex_color = f"#{r:02x}{g:02x}{b:02x}"
    if a != 255:
        hex_color += f"{a:02x}"
    return hex_color


def filter_figma_node(node: Any) -> Optional[Dict]:
    """Strip VECTOR nodes and clean up fills/strokes before returning to the AI."""
    if not isinstance(node, dict):
        return node

    if node.get("type") == "VECTOR":
        return None

    filtered: Dict[str, Any] = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
    }

    # Fills
    fills = node.get("fills")
    if fills and len(fills) > 0:
        processed_fills = []
        for fill in fills:
            pf = dict(fill)
            pf.pop("boundVariables", None)
            pf.pop("imageRef", None)

            if "gradientStops" in pf:
                new_stops = []
                for stop in pf["gradientStops"]:
                    ps = dict(stop)
                    if "color" in ps:
                        ps["color"] = rgba_to_hex(ps["color"])
                    ps.pop("boundVariables", None)
                    new_stops.append(ps)
                pf["gradientStops"] = new_stops

            if "color" in pf:
                pf["color"] = rgba_to_hex(pf["color"])

            processed_fills.append(pf)
        filtered["fills"] = processed_fills

    # Strokes
    strokes = node.get("strokes")
    if strokes and len(strokes) > 0:
        processed_strokes = []
        for stroke in strokes:
            ps = dict(stroke)
            ps.pop("boundVariables", None)
            if "color" in ps:
                ps["color"] = rgba_to_hex(ps["color"])
            processed_strokes.append(ps)
        filtered["strokes"] = processed_strokes

    if "cornerRadius" in node:
        filtered["cornerRadius"] = node["cornerRadius"]

    if "absoluteBoundingBox" in node:
        filtered["absoluteBoundingBox"] = node["absoluteBoundingBox"]

    if "characters" in node:
        filtered["characters"] = node["characters"]

    if "style" in node:
        s = node["style"]
        filtered["style"] = {
            "fontFamily": s.get("fontFamily"),
            "fontStyle": s.get("fontStyle"),
            "fontWeight": s.get("fontWeight"),
            "fontSize": s.get("fontSize"),
            "textAlignHorizontal": s.get("textAlignHorizontal"),
            "letterSpacing": s.get("letterSpacing"),
            "lineHeightPx": s.get("lineHeightPx"),
        }

    if "children" in node:
        children = [filter_figma_node(child) for child in node["children"]]
        filtered["children"] = [c for c in children if c is not None]

    return filtered


def process_figma_node_response(result: Any) -> Any:
    """Log node details to stderr and return *result* unchanged."""
    if not isinstance(result, dict):
        return result

    if "id" in result and isinstance(result["id"], str):
        logger.info(
            "Processed Figma node: %s (ID: %s)",
            result.get("name", "Unknown"),
            result["id"],
        )
        if "x" in result and "y" in result:
            logger.debug("Node position: (%s, %s)", result["x"], result["y"])
        if "width" in result and "height" in result:
            logger.debug(
                "Node dimensions: %sx%s", result["width"], result["height"]
            )

    return result


# ---------------------------------------------------------------------------
# WebSocket connection helpers
# ---------------------------------------------------------------------------

async def _listen() -> None:
    """Background task: read WS messages and resolve/reject pending futures."""
    import websockets  # imported here to keep top-level imports clean

    global ws_conn, pending_requests, current_channel

    try:
        async for raw in ws_conn:
            try:
                json_data = json.loads(raw)

                # Progress update — log and extend implicit timeout (asyncio
                # futures have no built-in timer, so we just log here).
                if json_data.get("type") == "progress_update":
                    msg = json_data.get("message", {})
                    progress_data = msg.get("data", {}) if isinstance(msg, dict) else {}
                    cmd_type = progress_data.get("commandType", "unknown")
                    progress = progress_data.get("progress", 0)
                    message = progress_data.get("message", "")
                    logger.info(
                        "Progress update for %s: %s%% - %s",
                        cmd_type,
                        progress,
                        message,
                    )
                    if (
                        progress_data.get("status") == "completed"
                        and progress_data.get("progress") == 100
                    ):
                        logger.info(
                            "Operation %s completed, waiting for final result",
                            cmd_type,
                        )
                    continue

                # Regular response
                my_response = json_data.get("message")
                logger.debug("Received message: %s", json.dumps(my_response))

                req_id = my_response.get("id") if isinstance(my_response, dict) else None
                if req_id and req_id in pending_requests:
                    future = pending_requests.pop(req_id)
                    if not future.done():
                        if my_response.get("error"):
                            logger.error(
                                "Error from Figma: %s", my_response["error"]
                            )
                            future.set_exception(
                                RuntimeError(my_response["error"])
                            )
                        elif my_response.get("result") is not None:
                            future.set_result(my_response["result"])
                        # else: no result and no error — ignore (shouldn't happen)
                else:
                    logger.info(
                        "Received broadcast message: %s",
                        json.dumps(my_response),
                    )

            except json.JSONDecodeError as exc:
                logger.error("Error parsing message: %s", exc)
            except Exception as exc:
                logger.error("Error handling message: %s", exc)

    except Exception as exc:
        logger.info("WebSocket listen loop ended: %s", exc)

    finally:
        # Connection dropped — reject all outstanding futures
        logger.info("Disconnected from Figma socket server")
        for req_id, future in list(pending_requests.items()):
            if not future.done():
                future.set_exception(RuntimeError("Connection closed"))
        pending_requests.clear()
        ws_conn = None


async def connect_to_figma(port: int = 3055) -> None:
    """Connect to the WebSocket relay server and start the background listener."""
    import websockets  # imported here to avoid circular issues at module load

    global ws_conn, _listen_task

    if ws_conn is not None:
        logger.info("Already connected to Figma")
        return

    ws_url = f"{WS_BASE}:{port}" if SERVER_URL == "localhost" else WS_BASE
    logger.info("Connecting to Figma socket server at %s...", ws_url)

    try:
        ws_conn = await websockets.connect(ws_url)
        logger.info("Connected to Figma socket server")
        _listen_task = asyncio.get_running_loop().create_task(_listen())
    except Exception as exc:
        logger.error("Failed to connect to Figma: %s", exc)
        ws_conn = None


async def join_channel(channel_name: str) -> None:
    """Send a join message and wait for the relay to confirm."""
    global current_channel

    if ws_conn is None:
        raise RuntimeError("Not connected to Figma")

    result = await send_command("join", {"channel": channel_name}, timeout_ms=30000)
    current_channel = channel_name
    logger.info("Joined channel: %s", channel_name)
    return result


async def send_command(
    command: str,
    params: Optional[Dict] = None,
    timeout_ms: int = 30000,
) -> Any:
    """Send a command to Figma via the relay and await the response."""
    if ws_conn is None:
        await connect_to_figma()
    if ws_conn is None:
        raise RuntimeError("Not connected to Figma. Is the relay server running?")

    is_join = command == "join"
    if not is_join and current_channel is None:
        raise RuntimeError("Must join a channel before sending commands")

    if params is None:
        params = {}

    req_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    pending_requests[req_id] = future

    request = {
        "id": req_id,
        "type": "join" if is_join else "message",
        "channel": params.get("channel") if is_join else current_channel,
        "message": {
            "id": req_id,
            "command": command,
            "params": {**params, "commandId": req_id},
        },
    }

    logger.info("Sending command to Figma: %s", command)
    logger.debug("Request details: %s", json.dumps(request))
    await ws_conn.send(json.dumps(request))

    # Await with timeout
    timeout_sec = timeout_ms / 1000.0
    try:
        result = await asyncio.wait_for(future, timeout=timeout_sec)
        return result
    except asyncio.TimeoutError:
        pending_requests.pop(req_id, None)
        raise RuntimeError(f"Request to Figma timed out after {timeout_ms // 1000}s")


# ---------------------------------------------------------------------------
# MCP response helpers
# ---------------------------------------------------------------------------

def ok(result: Any) -> List[TextContent]:
    """Wrap a successful result in an MCP TextContent list."""
    return [TextContent(type="text", text=json.dumps(result))]


def err(msg: str) -> List[TextContent]:
    """Wrap an error message in an MCP TextContent list."""
    return [TextContent(type="text", text=msg)]


def _stub(name: str) -> List[TextContent]:
    """Placeholder for tools that are not yet implemented."""
    return err(f"Tool '{name}' not yet implemented")


# ---------------------------------------------------------------------------
# Tool definitions (list_tools handler)
# ---------------------------------------------------------------------------

ALL_TOOLS: List[Tool] = [
    # ── Group A: Document & Selection ──────────────────────────────────────
    Tool(
        name="get_document_info",
        description="Get detailed information about the current Figma document",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_selection",
        description="Get information about the current selection in Figma",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="read_my_design",
        description=(
            "Get detailed information about the current selection in Figma, "
            "including all node details"
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_node_info",
        description="Get detailed information about a specific node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {
                    "type": "string",
                    "description": "The ID of the node to get information about",
                }
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="get_nodes_info",
        description="Get detailed information about multiple nodes in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs to get information about",
                }
            },
            "required": ["nodeIds"],
        },
    ),
    Tool(
        name="get_styles",
        description="Get all styles defined in the current Figma document",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_local_components",
        description="Get all local components defined in the current Figma document",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_annotations",
        description="Get annotations from the current Figma document or a specific node",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {
                    "type": "string",
                    "description": "The ID of the node to get annotations for (optional)",
                },
                "includeCategories": {
                    "type": "boolean",
                    "description": "Whether to include annotation categories",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_reactions",
        description="Get reactions (interactions/prototyping) for specified nodes",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs to get reactions for",
                }
            },
            "required": ["nodeIds"],
        },
    ),
    # ── Group B: Create & Modify ────────────────────────────────────────────
    Tool(
        name="create_rectangle",
        description="Create a new rectangle node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X position"},
                "y": {"type": "number", "description": "Y position"},
                "width": {"type": "number", "description": "Width"},
                "height": {"type": "number", "description": "Height"},
                "name": {"type": "string", "description": "Name of the rectangle (optional)"},
                "parentId": {"type": "string", "description": "Parent node ID (optional)"},
            },
            "required": ["x", "y", "width", "height"],
        },
    ),
    Tool(
        name="create_frame",
        description="Create a new frame node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X position"},
                "y": {"type": "number", "description": "Y position"},
                "width": {"type": "number", "description": "Width"},
                "height": {"type": "number", "description": "Height"},
                "name": {"type": "string", "description": "Name of the frame (optional)"},
                "parentId": {"type": "string", "description": "Parent node ID (optional)"},
                "fillColor": {"type": "object", "description": "Fill color as RGBA {r,g,b,a} with 0-1 values"},
                "strokeColor": {"type": "object", "description": "Stroke color as RGBA {r,g,b,a}"},
                "strokeWeight": {"type": "number", "description": "Stroke weight in pixels"},
                "layoutMode": {"type": "string", "description": "Auto layout mode: HORIZONTAL, VERTICAL, or NONE"},
                "layoutWrap": {"type": "string", "description": "Layout wrap: NO_WRAP or WRAP"},
                "paddingTop": {"type": "number", "description": "Top padding"},
                "paddingRight": {"type": "number", "description": "Right padding"},
                "paddingBottom": {"type": "number", "description": "Bottom padding"},
                "paddingLeft": {"type": "number", "description": "Left padding"},
                "primaryAxisAlignItems": {"type": "string", "description": "Primary axis alignment"},
                "counterAxisAlignItems": {"type": "string", "description": "Counter axis alignment"},
                "layoutSizingHorizontal": {"type": "string", "description": "Horizontal sizing mode"},
                "layoutSizingVertical": {"type": "string", "description": "Vertical sizing mode"},
                "itemSpacing": {"type": "number", "description": "Item spacing in auto layout"},
            },
            "required": ["x", "y", "width", "height"],
        },
    ),
    Tool(
        name="create_text",
        description="Create a new text node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X position"},
                "y": {"type": "number", "description": "Y position"},
                "text": {"type": "string", "description": "Text content"},
                "fontSize": {"type": "number", "description": "Font size (optional)"},
                "fontWeight": {"type": "number", "description": "Font weight (optional)"},
                "fontColor": {"type": "object", "description": "Font color as RGBA {r,g,b,a} with 0-1 values. Defaults to black."},
                "name": {"type": "string", "description": "Name of the text node (optional)"},
                "parentId": {"type": "string", "description": "Parent node ID (optional)"},
            },
            "required": ["x", "y", "text"],
        },
    ),
    Tool(
        name="create_component_instance",
        description="Create an instance of an existing component in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "componentKey": {
                    "type": "string",
                    "description": "Key of the component to instantiate",
                },
                "x": {"type": "number", "description": "X position (optional)"},
                "y": {"type": "number", "description": "Y position (optional)"},
            },
            "required": ["componentKey"],
        },
    ),
    Tool(
        name="clone_node",
        description="Clone an existing node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node to clone"},
                "x": {"type": "number", "description": "X position for the clone (optional)"},
                "y": {"type": "number", "description": "Y position for the clone (optional)"},
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="delete_node",
        description="Delete a node from the Figma document",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node to delete"}
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="delete_multiple_nodes",
        description="Delete multiple nodes from the Figma document",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs to delete",
                }
            },
            "required": ["nodeIds"],
        },
    ),
    # ── Group C: Style & Appearance ─────────────────────────────────────────
    Tool(
        name="set_fill_color",
        description="Set the fill color of a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node"},
                "r": {"type": "number", "description": "Red (0-1)"},
                "g": {"type": "number", "description": "Green (0-1)"},
                "b": {"type": "number", "description": "Blue (0-1)"},
                "a": {"type": "number", "description": "Alpha (0-1, optional)"},
            },
            "required": ["nodeId", "r", "g", "b"],
        },
    ),
    Tool(
        name="set_stroke_color",
        description="Set the stroke color of a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node"},
                "r": {"type": "number", "description": "Red (0-1)"},
                "g": {"type": "number", "description": "Green (0-1)"},
                "b": {"type": "number", "description": "Blue (0-1)"},
                "a": {"type": "number", "description": "Alpha (0-1, optional)"},
                "weight": {
                    "type": "number",
                    "description": "Stroke weight in pixels (optional)",
                },
            },
            "required": ["nodeId", "r", "g", "b"],
        },
    ),
    Tool(
        name="set_corner_radius",
        description="Set the corner radius of a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node"},
                "radius": {"type": "number", "description": "Corner radius value"},
                "topLeft": {
                    "type": "number",
                    "description": "Top-left corner radius (optional)",
                },
                "topRight": {
                    "type": "number",
                    "description": "Top-right corner radius (optional)",
                },
                "bottomLeft": {
                    "type": "number",
                    "description": "Bottom-left corner radius (optional)",
                },
                "bottomRight": {
                    "type": "number",
                    "description": "Bottom-right corner radius (optional)",
                },
            },
            "required": ["nodeId", "radius"],
        },
    ),
    Tool(
        name="set_text_content",
        description="Set the text content of a text node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the text node"},
                "text": {"type": "string", "description": "New text content"},
            },
            "required": ["nodeId", "text"],
        },
    ),
    Tool(
        name="set_multiple_text_contents",
        description="Set the text content of multiple text nodes in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {
                    "type": "string",
                    "description": "ID of the parent node (used as context)",
                },
                "text": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nodeId": {"type": "string"},
                            "text": {"type": "string"},
                        },
                        "required": ["nodeId", "text"],
                    },
                    "description": "List of nodeId/text pairs to update",
                },
            },
            "required": ["nodeId", "text"],
        },
    ),
    Tool(
        name="export_node_as_image",
        description="Export a node as an image from Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node to export"},
                "format": {
                    "type": "string",
                    "description": "Export format: PNG, JPG, SVG, PDF (optional)",
                },
                "scale": {"type": "number", "description": "Export scale (optional)"},
            },
            "required": ["nodeId"],
        },
    ),
    # ── Group D: Layout & Positioning ──────────────────────────────────────
    Tool(
        name="move_node",
        description="Move a node to a new position in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node to move"},
                "x": {"type": "number", "description": "New X position"},
                "y": {"type": "number", "description": "New Y position"},
            },
            "required": ["nodeId", "x", "y"],
        },
    ),
    Tool(
        name="resize_node",
        description="Resize a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node to resize"},
                "width": {"type": "number", "description": "New width"},
                "height": {"type": "number", "description": "New height"},
            },
            "required": ["nodeId", "width", "height"],
        },
    ),
    Tool(
        name="set_layout_mode",
        description="Set the auto-layout mode of a frame node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the frame node"},
                "layoutMode": {
                    "type": "string",
                    "description": "Layout mode: NONE, HORIZONTAL, or VERTICAL",
                },
            },
            "required": ["nodeId", "layoutMode"],
        },
    ),
    Tool(
        name="set_padding",
        description="Set the padding of an auto-layout frame in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the frame node"},
                "top": {"type": "number", "description": "Top padding (optional)"},
                "right": {"type": "number", "description": "Right padding (optional)"},
                "bottom": {"type": "number", "description": "Bottom padding (optional)"},
                "left": {"type": "number", "description": "Left padding (optional)"},
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="set_axis_align",
        description="Set the axis alignment of an auto-layout frame in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the frame node"},
                "primaryAxisAlignItems": {
                    "type": "string",
                    "description": "Primary axis alignment (optional)",
                },
                "counterAxisAlignItems": {
                    "type": "string",
                    "description": "Counter axis alignment (optional)",
                },
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="set_layout_sizing",
        description="Set the layout sizing of a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node"},
                "horizontalSizing": {
                    "type": "string",
                    "description": "Horizontal sizing mode (optional)",
                },
                "verticalSizing": {
                    "type": "string",
                    "description": "Vertical sizing mode (optional)",
                },
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="set_item_spacing",
        description="Set the item spacing of an auto-layout frame in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the frame node"},
                "spacing": {"type": "number", "description": "Item spacing value"},
            },
            "required": ["nodeId", "spacing"],
        },
    ),
    # ── Group E: Annotations, Connections & Channel ─────────────────────────
    Tool(
        name="set_annotation",
        description="Set an annotation on a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node"},
                "labelMarkdown": {
                    "type": "string",
                    "description": "Annotation label in Markdown",
                },
                "annotationId": {
                    "type": "string",
                    "description": "ID of an existing annotation to update (optional)",
                },
                "categoryId": {
                    "type": "string",
                    "description": "Category ID for the annotation (optional)",
                },
                "properties": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Additional annotation properties (optional)",
                },
            },
            "required": ["nodeId", "labelMarkdown"],
        },
    ),
    Tool(
        name="set_multiple_annotations",
        description="Set annotations on multiple nodes in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "annotations": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of annotation objects",
                }
            },
            "required": ["annotations"],
        },
    ),
    Tool(
        name="scan_text_nodes",
        description="Scan text nodes within a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the node to scan"},
                "useChunking": {
                    "type": "boolean",
                    "description": "Whether to use chunked scanning (optional)",
                },
                "chunkSize": {
                    "type": "integer",
                    "description": "Chunk size for chunked scanning (optional)",
                },
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="scan_nodes_by_types",
        description="Scan nodes by their type within a node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {"type": "string", "description": "ID of the parent node"},
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node types to scan for",
                },
            },
            "required": ["nodeId", "types"],
        },
    ),
    Tool(
        name="get_instance_overrides",
        description="Get overrides from a component instance in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {
                    "type": "string",
                    "description": "ID of the component instance",
                }
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="set_instance_overrides",
        description="Apply instance overrides from one instance to multiple target instances",
        inputSchema={
            "type": "object",
            "properties": {
                "sourceNodeId": {
                    "type": "string",
                    "description": "ID of the source instance to copy overrides from",
                },
                "targetNodeIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IDs of target instances to apply overrides to",
                },
            },
            "required": ["sourceNodeId", "targetNodeIds"],
        },
    ),
    Tool(
        name="set_default_connector",
        description="Set the default connector style for new connections in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "connectorId": {
                    "type": "string",
                    "description": "ID of the connector node to use as default (optional)",
                }
            },
            "required": [],
        },
    ),
    Tool(
        name="create_connections",
        description="Create connections (connectors) between nodes in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "connections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "startNodeId": {"type": "string"},
                            "endNodeId": {"type": "string"},
                            "text": {"type": "string"},
                        },
                        "required": ["startNodeId", "endNodeId"],
                    },
                    "description": "List of connections to create",
                }
            },
            "required": ["connections"],
        },
    ),
    Tool(
        name="set_focus",
        description="Set the viewport focus on a specific node in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeId": {
                    "type": "string",
                    "description": "ID of the node to focus on",
                }
            },
            "required": ["nodeId"],
        },
    ),
    Tool(
        name="set_selections",
        description="Set the current selection to a list of nodes in Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "nodeIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs to select",
                }
            },
            "required": ["nodeIds"],
        },
    ),
    Tool(
        name="join_channel",
        description="Join a specific channel to communicate with Figma",
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "The name of the channel to join",
                    "default": "",
                }
            },
            "required": [],
        },
    ),
]

_TOOL_NAME_SET = {t.name for t in ALL_TOOLS}


# ---------------------------------------------------------------------------
# MCP handler: list_tools
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> List[Tool]:
    return ALL_TOOLS


# ---------------------------------------------------------------------------
# MCP handler: call_tool
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    # ── Group A ──────────────────────────────────────────────────────────────
    if name == "get_document_info":
        try:
            result = await send_command("get_document_info")
            return ok(result)
        except Exception as e:
            return err(f"Error getting document info: {e}")
    elif name == "get_selection":
        try:
            result = await send_command("get_selection")
            return ok(result)
        except Exception as e:
            return err(f"Error getting selection: {e}")
    elif name == "read_my_design":
        try:
            result = await send_command("read_my_design", {})
            return ok(result)
        except Exception as e:
            return err(f"Error reading design: {e}")
    elif name == "get_node_info":
        try:
            if not arguments or "nodeId" not in arguments:
                return err("Missing required parameter: nodeId")
            node_id: str = arguments["nodeId"]
            result = await send_command("get_node_info", {"nodeId": node_id})
            return ok(filter_figma_node(result))
        except Exception as e:
            return err(f"Error getting node info: {e}")
    elif name == "get_nodes_info":
        try:
            if not arguments or "nodeIds" not in arguments:
                return err("Missing required parameter: nodeIds")
            node_ids: List[str] = arguments["nodeIds"]
            results = await asyncio.gather(
                *[send_command("get_node_info", {"nodeId": nid}) for nid in node_ids],
                return_exceptions=True
            )
            filtered = [
                filter_figma_node(r)
                for r in results
                if not isinstance(r, Exception)
            ]
            filtered = [f for f in filtered if f is not None]
            return ok(filtered)
        except Exception as e:
            return err(f"Error getting nodes info: {e}")
    elif name == "get_styles":
        try:
            result = await send_command("get_styles")
            return ok(result)
        except Exception as e:
            return err(f"Error getting styles: {e}")
    elif name == "get_local_components":
        try:
            result = await send_command("get_local_components")
            return ok(result)
        except Exception as e:
            return err(f"Error getting local components: {e}")
    elif name == "get_annotations":
        try:
            params: Dict[str, Any] = {}
            if arguments and "nodeId" in arguments:
                params["nodeId"] = arguments["nodeId"]
            if arguments and "includeCategories" in arguments:
                params["includeCategories"] = arguments["includeCategories"]
            result = await send_command("get_annotations", params)
            return ok(result)
        except Exception as e:
            return err(f"Error getting annotations: {e}")
    elif name == "get_reactions":
        try:
            if not arguments or "nodeIds" not in arguments:
                return err("Missing required parameter: nodeIds")
            node_ids = arguments["nodeIds"]
            result = await send_command("get_reactions", {"nodeIds": node_ids})
            return ok(result)
        except Exception as e:
            return err(f"Error getting reactions: {e}")
    # ── Group B ──────────────────────────────────────────────────────────────
    elif name == "create_rectangle":
        try:
            x = arguments.get("x")
            y = arguments.get("y")
            width = arguments.get("width")
            height = arguments.get("height")
            if x is None or y is None or width is None or height is None:
                return err("create_rectangle requires x, y, width, and height")
            params: Dict[str, Any] = {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "name": arguments.get("name") or "Rectangle",
            }
            parent_id = arguments.get("parentId")
            if parent_id is not None:
                params["parentId"] = parent_id
            result = await send_command("create_rectangle", params)
            return ok(result)
        except Exception as e:
            return err(f"Error creating rectangle: {e}")
    elif name == "create_frame":
        try:
            x = arguments.get("x")
            y = arguments.get("y")
            width = arguments.get("width")
            height = arguments.get("height")
            if x is None or y is None or width is None or height is None:
                return err("create_frame requires x, y, width, and height")
            params: Dict[str, Any] = {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "name": arguments.get("name") or "Frame",
                "fillColor": arguments.get("fillColor") or {"r": 1, "g": 1, "b": 1, "a": 1},
            }
            for opt_key in (
                "parentId",
                "strokeColor",
                "strokeWeight",
                "layoutMode",
                "layoutWrap",
                "paddingTop",
                "paddingRight",
                "paddingBottom",
                "paddingLeft",
                "primaryAxisAlignItems",
                "counterAxisAlignItems",
                "layoutSizingHorizontal",
                "layoutSizingVertical",
                "itemSpacing",
            ):
                val = arguments.get(opt_key)
                if val is not None:
                    params[opt_key] = val
            result = await send_command("create_frame", params)
            return ok(result)
        except Exception as e:
            return err(f"Error creating frame: {e}")
    elif name == "create_text":
        try:
            x = arguments.get("x")
            y = arguments.get("y")
            text = arguments.get("text")
            if x is None or y is None or text is None:
                return err("create_text requires x, y, and text")
            params: Dict[str, Any] = {
                "x": x,
                "y": y,
                "text": text,
                "fontSize": arguments.get("fontSize") if arguments.get("fontSize") is not None else 14,
                "fontWeight": arguments.get("fontWeight") if arguments.get("fontWeight") is not None else 400,
                "fontColor": arguments.get("fontColor") or {"r": 0, "g": 0, "b": 0, "a": 1},
                "name": arguments.get("name") or "Text",
            }
            parent_id = arguments.get("parentId")
            if parent_id is not None:
                params["parentId"] = parent_id
            result = await send_command("create_text", params)
            return ok(result)
        except Exception as e:
            return err(f"Error creating text: {e}")
    elif name == "create_component_instance":
        try:
            component_id = arguments.get("componentKey")
            if not component_id:
                return err("create_component_instance requires componentKey")
            params: Dict[str, Any] = {"componentKey": component_id}
            x = arguments.get("x")
            if x is not None:
                params["x"] = x
            y = arguments.get("y")
            if y is not None:
                params["y"] = y
            result = await send_command("create_component_instance", params)
            return ok(result)
        except Exception as e:
            return err(f"Error creating component instance: {e}")
    elif name == "clone_node":
        try:
            node_id = arguments.get("nodeId")
            if not node_id:
                return err("clone_node requires nodeId")
            params: Dict[str, Any] = {"nodeId": node_id}
            x = arguments.get("x")
            if x is not None:
                params["x"] = x
            y = arguments.get("y")
            if y is not None:
                params["y"] = y
            result = await send_command("clone_node", params)
            return ok(result)
        except Exception as e:
            return err(f"Error cloning node: {e}")
    elif name == "delete_node":
        try:
            node_id = arguments.get("nodeId")
            if not node_id:
                return err("delete_node requires nodeId")
            await send_command("delete_node", {"nodeId": node_id})
            return ok({"deleted": node_id})
        except Exception as e:
            return err(f"Error deleting node: {e}")
    elif name == "delete_multiple_nodes":
        try:
            node_ids = arguments.get("nodeIds")
            if not node_ids:
                return err("delete_multiple_nodes requires nodeIds")
            result = await send_command("delete_multiple_nodes", {"nodeIds": node_ids})
            return ok(result)
        except Exception as e:
            return err(f"Error deleting multiple nodes: {e}")
    # ── Group C ──────────────────────────────────────────────────────────────
    elif name == "set_fill_color":
        try:
            node_id = arguments.get("nodeId")
            r = arguments.get("r")
            g = arguments.get("g")
            b = arguments.get("b")
            if node_id is None or r is None or g is None or b is None:
                return err("set_fill_color requires nodeId, r, g, and b")
            a_val = arguments.get("a")
            a = a_val if a_val is not None else 1
            result = await send_command("set_fill_color", {
                "nodeId": node_id,
                "color": {"r": r, "g": g, "b": b, "a": a},
            })
            typed = result if isinstance(result, dict) else {}
            node_name = typed.get("name", node_id)
            return [TextContent(type="text", text=f'Set fill color of node "{node_name}" to RGBA({r}, {g}, {b}, {a})')]
        except Exception as e:
            return err(f"Error setting fill color: {e}")
    elif name == "set_stroke_color":
        try:
            node_id = arguments.get("nodeId")
            r = arguments.get("r")
            g = arguments.get("g")
            b = arguments.get("b")
            if node_id is None or r is None or g is None or b is None:
                return err("set_stroke_color requires nodeId, r, g, and b")
            a_val = arguments.get("a")
            a = a_val if a_val is not None else 1
            weight_val = arguments.get("weight")
            weight = weight_val if weight_val is not None else 1
            result = await send_command("set_stroke_color", {
                "nodeId": node_id,
                "color": {"r": r, "g": g, "b": b, "a": a},
                "weight": weight,
            })
            typed = result if isinstance(result, dict) else {}
            node_name = typed.get("name", node_id)
            return [TextContent(type="text", text=f'Set stroke color of node "{node_name}" to RGBA({r}, {g}, {b}, {a}) with weight {weight}')]
        except Exception as e:
            return err(f"Error setting stroke color: {e}")
    elif name == "set_corner_radius":
        try:
            node_id = arguments.get("nodeId")
            radius = arguments.get("radius")
            if node_id is None or radius is None:
                return err("set_corner_radius requires nodeId and radius")
            corners = arguments.get("corners")
            result = await send_command("set_corner_radius", {
                "nodeId": node_id,
                "radius": radius,
                "corners": corners if corners is not None else [True, True, True, True],
            })
            typed = result if isinstance(result, dict) else {}
            node_name = typed.get("name", node_id)
            return [TextContent(type="text", text=f'Set corner radius of node "{node_name}" to {radius}px')]
        except Exception as e:
            return err(f"Error setting corner radius: {e}")
    elif name == "set_text_content":
        try:
            node_id = arguments.get("nodeId")
            text = arguments.get("text")
            if node_id is None or text is None:
                return err("set_text_content requires nodeId and text")
            result = await send_command("set_text_content", {"nodeId": node_id, "text": text})
            typed = result if isinstance(result, dict) else {}
            node_name = typed.get("name", node_id)
            return [TextContent(type="text", text=f'Updated text content of node "{node_name}" to "{text}"')]
        except Exception as e:
            return err(f"Error setting text content: {e}")
    elif name == "set_multiple_text_contents":
        try:
            node_id = arguments.get("nodeId")
            text = arguments.get("text")
            if node_id is None:
                return err("set_multiple_text_contents requires nodeId")
            if not text:
                return [TextContent(type="text", text="No text provided")]
            total = len(text)
            chunk_size = 5
            all_results: List[Any] = []
            chunks_processed = 0
            for chunk_start in range(0, total, chunk_size):
                chunk = text[chunk_start: chunk_start + chunk_size]
                chunk_result = await send_command("set_multiple_text_contents", {
                    "nodeId": node_id,
                    "text": chunk,
                })
                all_results.append(chunk_result)
                chunks_processed += 1
            replacements_applied = 0
            replacements_failed = 0
            failed_nodes: List[str] = []
            for chunk_result in all_results:
                if isinstance(chunk_result, dict):
                    replacements_applied += chunk_result.get("replacementsApplied", 0)
                    replacements_failed += chunk_result.get("replacementsFailed", 0)
                    for item in chunk_result.get("results", []):
                        if isinstance(item, dict) and not item.get("success"):
                            failed_nodes.append(f"- {item.get('nodeId', '?')}: {item.get('error', 'Unknown error')}")
            progress_text = (
                f"Text replacement completed:\n"
                f"- {replacements_applied} of {total} successfully updated\n"
                f"- {replacements_failed} failed\n"
                f"- Processed in {chunks_processed} batches"
            )
            if failed_nodes:
                progress_text += "\n\nNodes that failed:\n" + "\n".join(failed_nodes)
            return [
                TextContent(type="text", text=f"Starting text replacement for {total} nodes. This will be processed in batches of {chunk_size}..."),
                TextContent(type="text", text=progress_text),
            ]
        except Exception as e:
            return err(f"Error setting multiple text contents: {e}")
    elif name == "export_node_as_image":
        try:
            node_id = arguments.get("nodeId")
            if node_id is None:
                return err("export_node_as_image requires nodeId")
            fmt_val = arguments.get("format")
            fmt = fmt_val if fmt_val is not None else "PNG"
            scale_val = arguments.get("scale")
            scale = scale_val if scale_val is not None else 1
            result = await send_command("export_node_as_image", {
                "nodeId": node_id,
                "format": fmt,
                "scale": scale,
            })
            typed = result if isinstance(result, dict) else {}
            image_data = typed.get("imageData", "")
            mime_type = typed.get("mimeType", "image/png")
            return [types.ImageContent(type="image", data=image_data, mimeType=mime_type)]
        except Exception as e:
            return err(f"Error exporting node as image: {e}")
    # ── Group D ──────────────────────────────────────────────────────────────
    elif name == "move_node":
        return _stub(name)
    elif name == "resize_node":
        return _stub(name)
    elif name == "set_layout_mode":
        return _stub(name)
    elif name == "set_padding":
        return _stub(name)
    elif name == "set_axis_align":
        return _stub(name)
    elif name == "set_layout_sizing":
        return _stub(name)
    elif name == "set_item_spacing":
        return _stub(name)
    # ── Group E ──────────────────────────────────────────────────────────────
    elif name == "set_annotation":
        return _stub(name)
    elif name == "set_multiple_annotations":
        return _stub(name)
    elif name == "scan_text_nodes":
        return _stub(name)
    elif name == "scan_nodes_by_types":
        return _stub(name)
    elif name == "get_instance_overrides":
        return _stub(name)
    elif name == "set_instance_overrides":
        return _stub(name)
    elif name == "set_default_connector":
        return _stub(name)
    elif name == "create_connections":
        return _stub(name)
    elif name == "set_focus":
        return _stub(name)
    elif name == "set_selections":
        return _stub(name)
    elif name == "join_channel":
        try:
            channel = arguments.get("channel", "")
            if not channel:
                return err("Please provide a channel name to join")
            await join_channel(channel)
            return ok(f"Successfully joined channel: {channel}")
        except Exception as e:
            return err(f"Error joining channel: {e}")
    else:
        return err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    await connect_to_figma()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
