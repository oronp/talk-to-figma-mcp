# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

MCP integration between AI assistants (Cursor, Claude, etc.) and Figma. Enables reading and programmatically modifying Figma designs through a 3-component architecture.

## Commands

```bash
bun install          # Install dependencies
bun run build        # Build MCP server → dist/
bun run dev          # Build in watch mode
bun socket           # Start WebSocket relay server
bun start            # Run built MCP server
bun setup            # Create .cursor/mcp.json pointing to latest published package
bun run pub:release  # Build + publish to npm
```

No test suite is present in this repository.

## Architecture

```
AI Assistant (MCP Client)
    ↕ stdio (MCP protocol)
src/talk_to_figma_mcp/server.ts  ← MCP server
    ↕ WebSocket (port 3055)
src/socket.ts                    ← WebSocket relay server
    ↕ WebSocket (port 3055)
src/cursor_mcp_plugin/           ← Figma plugin (code.js + ui.html)
    ↕ Figma Plugin API
Figma Document
```

**`src/talk_to_figma_mcp/server.ts`** — ~3100 lines, single file with all MCP tool definitions. Has its own nested `package.json` + `bun.lock` in `src/talk_to_figma_mcp/`. Accepts `--server=<hostname>` CLI arg; `localhost` uses `ws://`, anything else uses `wss://`.

**`src/socket.ts`** — Bun native WebSocket server. For local dev: omit `SSL_KEY_PATH`/`SSL_CERT_PATH` env vars or it crashes. For Windows/WSL: uncomment `hostname: "0.0.0.0"`.

**`src/cursor_mcp_plugin/`** — Plain JS, no build step. `code.js` = Figma plugin sandbox (all Figma API calls), `ui.html` = holds the WebSocket connection to the relay.

## Dev Setup

For local development, point MCP config to the source file directly:
```json
{
  "mcpServers": {
    "TalkToFigma": {
      "command": "bun",
      "args": ["/path-to-repo/src/talk_to_figma_mcp/server.ts"]
    }
  }
}
```

`bun setup` creates `.cursor/mcp.json` pointing to `bunx cursor-talk-to-figma-mcp@latest` for production use.

**Docker**: `Dockerfile` builds and runs the MCP server, exposed on port 3055.

**Smithery**: `smithery.yaml` configures deployment via `bunx cursor-talk-to-figma-mcp`.

**`DRAGME.md`**: End-user AI setup guide — not codebase documentation, ignore when working on the project.

## Gotchas

- **Tool name contract**: MCP tool names in `server.ts` must exactly match command names expected by `code.js` — they share a string contract.
- **`join_channel` first**: Must be called before any other tool; the server enforces this with `currentChannel` state.
- **Relay is separate**: The relay server (`bun socket`) does not run as part of the MCP server — start it independently.
- **Manifest network lock**: `src/cursor_mcp_plugin/manifest.json` only permits `ws://localhost:3055`. Update it for remote/WSS deployments.
