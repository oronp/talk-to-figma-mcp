# Python Port Design — talk-to-figma-mcp

**Date:** 2026-02-25
**Goal:** Port the TypeScript MCP server and WebSocket relay to Python for personal use/learning, living alongside the existing TypeScript code in this repo.

---

## Folder Structure

```
src/python_mcp/
├── server.py          # MCP server — port of src/talk_to_figma_mcp/server.ts
├── socket_server.py   # WebSocket relay — port of src/socket.ts
├── requirements.txt   # mcp, websockets
└── README.md          # How to run
```

The Figma plugin (`src/cursor_mcp_plugin/`) is untouched — it must remain JavaScript.

---

## Dependencies

- `mcp` — official Python MCP SDK (asyncio-based, mirrors the TypeScript SDK)
- `websockets` — asyncio WebSocket client + server
- `uuid` — stdlib

---

## Implementation Approach: Skeleton-first, then parallel fill-in

**Phase 1 — Skeleton agent (sequential):**
One agent creates `server.py` with all 40 tool stubs and the full shared infrastructure, plus `socket_server.py`. No tool logic yet — each tool raises `NotImplementedError` or returns a placeholder.

**Phase 2 — Tool agents (parallel, 5 agents):**
Each agent fills in one group of tool stubs by reading the corresponding TypeScript and translating to Python. They only edit `server.py` — no shared state conflicts.

**Phase 3 — Integration (sequential):**
Wire everything together, verify imports, test end-to-end.

---

## Shared Infrastructure (`server.py`)

| TypeScript | Python |
|---|---|
| `McpServer` + `StdioServerTransport` | `mcp.server.Server` + `stdio_server()` |
| `ws` WebSocket client | `websockets.connect()` |
| `pendingRequests: Map<string, {resolve, reject, timeout}>` | `pending_requests: dict[str, asyncio.Future]` |
| `currentChannel: string \| null` | `current_channel: str \| None` |
| `sendCommandToFigma(cmd, params, timeout)` | `async send_command(cmd, params, timeout)` |
| `connectToFigma()` + reconnect logic | `async connect_to_figma()` with retry |
| `filterFigmaNode()` + `processFigmaNodeResponse()` | `filter_figma_node()` + `process_response()` |
| `rgbaToHex()` | `rgba_to_hex()` |
| `logger` → stderr | `logging` module → stderr handler |
| `--server=<hostname>` CLI arg | `argparse` |

**Tool pattern:**
```python
@server.call_tool()
async def get_document_info(arguments: dict) -> list[TextContent]:
    try:
        result = await send_command("get_document_info")
        return [TextContent(type="text", text=json.dumps(result))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]
```

---

## Tool Groups

| Agent | Group | Tools |
|---|---|---|
| Agent 1 | Document & Selection | `get_document_info`, `get_selection`, `read_my_design`, `get_node_info`, `get_nodes_info`, `get_styles`, `get_local_components`, `get_annotations`, `get_reactions` |
| Agent 2 | Create & Modify | `create_rectangle`, `create_frame`, `create_text`, `create_component_instance`, `clone_node`, `delete_node`, `delete_multiple_nodes` |
| Agent 3 | Style & Appearance | `set_fill_color`, `set_stroke_color`, `set_corner_radius`, `set_text_content`, `set_multiple_text_contents`, `export_node_as_image` |
| Agent 4 | Layout & Positioning | `move_node`, `resize_node`, `set_layout_mode`, `set_padding`, `set_axis_align`, `set_layout_sizing`, `set_item_spacing` |
| Agent 5 | Annotations, Connections & Channel | `set_annotation`, `set_multiple_annotations`, `scan_text_nodes`, `scan_nodes_by_types`, `set_instance_overrides`, `get_instance_overrides`, `set_default_connector`, `create_connections`, `set_focus`, `set_selections`, `join_channel` |

---

## Data Flow

```
AI (MCP client)
  → stdio → server.py
      → asyncio WebSocket → socket_server.py (relay)
          → WebSocket → Figma plugin (code.js)
              → Figma API
          ← result JSON
      ← future resolved by UUID match
  ← TextContent response
```

## Key Behaviors

- `send_command()` generates UUID → stores `asyncio.Future` in `pending_requests` → sends JSON → awaits with timeout (30s, extends to 60s on progress)
- WebSocket message handler resolves/rejects future by UUID
- `join_channel` enforced first: check `current_channel is None`
- All logs → `stderr` (never stdout — would corrupt MCP stdio transport)
- `socket_server.py`: asyncio WebSocket server, channel-based pub/sub, mirrors `socket.ts`
