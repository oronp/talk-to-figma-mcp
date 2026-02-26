#!/usr/bin/env python3
"""
WebSocket relay server for talk-to-figma-mcp.

Port of src/socket.ts — implements a channel-based pub/sub relay so the MCP
server and the Figma plugin can communicate without a direct connection.

Usage:
    python socket_server.py          # listens on port 3055
    PORT=4000 python socket_server.py
"""

import asyncio
import json
import os
import sys
from typing import Dict, Set

import websockets
from websockets.server import ServerConnection

# channel_name -> set of connected ServerConnection objects
channels: Dict[str, Set[ServerConnection]] = {}


async def handler(ws: ServerConnection) -> None:
    """Handle a single WebSocket client connection."""
    print("New client connected", flush=True)

    # Send welcome message
    await ws.send(json.dumps({
        "type": "system",
        "message": "Please join a channel to start chatting",
    }))

    try:
        async for raw_message in ws:
            try:
                print(f"Received message from client: {raw_message}", flush=True)
                data = json.loads(raw_message)

                # ── JOIN ──────────────────────────────────────────────────────
                if data.get("type") == "join":
                    channel_name = data.get("channel")
                    if not channel_name or not isinstance(channel_name, str):
                        await ws.send(json.dumps({
                            "type": "error",
                            "message": "Channel name is required",
                        }))
                        continue

                    # Create channel if it does not exist yet
                    if channel_name not in channels:
                        channels[channel_name] = set()

                    channel_clients = channels[channel_name]
                    channel_clients.add(ws)

                    # Confirmation 1: plain join confirmation
                    await ws.send(json.dumps({
                        "type": "system",
                        "message": f"Joined channel: {channel_name}",
                        "channel": channel_name,
                    }))

                    # Confirmation 2: result keyed by request id
                    print(f"Sending message to client: {data.get('id')}", flush=True)
                    await ws.send(json.dumps({
                        "type": "system",
                        "message": {
                            "id": data.get("id"),
                            "result": f"Connected to channel: {channel_name}",
                        },
                        "channel": channel_name,
                    }))

                    # Notify other members of the channel
                    for client in list(channel_clients):
                        if client is not ws:
                            try:
                                await client.send(json.dumps({
                                    "type": "system",
                                    "message": "A new user has joined the channel",
                                    "channel": channel_name,
                                }))
                            except Exception:
                                channel_clients.discard(client)

                # ── MESSAGE ───────────────────────────────────────────────────
                elif data.get("type") == "message":
                    channel_name = data.get("channel")
                    if not channel_name or not isinstance(channel_name, str):
                        await ws.send(json.dumps({
                            "type": "error",
                            "message": "Channel name is required",
                        }))
                        continue

                    channel_clients = channels.get(channel_name)
                    if channel_clients is None or ws not in channel_clients:
                        await ws.send(json.dumps({
                            "type": "error",
                            "message": "You must join the channel first",
                        }))
                        continue

                    # Broadcast to every member of the channel (including sender)
                    for client in list(channel_clients):
                        try:
                            print(
                                f"Broadcasting message to client: {data.get('message')}",
                                flush=True,
                            )
                            await client.send(json.dumps({
                                "type": "broadcast",
                                "message": data.get("message"),
                                "sender": "You" if client is ws else "User",
                                "channel": channel_name,
                            }))
                        except Exception:
                            channel_clients.discard(client)

            except json.JSONDecodeError as exc:
                print(f"Error handling message: {exc}", flush=True)
            except Exception as exc:
                print(f"Error handling message: {exc}", flush=True)

    except websockets.exceptions.ConnectionClosedError:
        pass
    except websockets.exceptions.ConnectionClosedOK:
        pass
    finally:
        # Remove this client from every channel it was part of
        print("Client disconnected", flush=True)
        for channel_name, clients in list(channels.items()):
            if ws in clients:
                clients.discard(ws)
                # Notify remaining members
                for client in list(clients):
                    try:
                        await client.send(json.dumps({
                            "type": "system",
                            "message": "A user has left the channel",
                            "channel": channel_name,
                        }))
                    except Exception:
                        clients.discard(client)


async def main() -> None:
    port = int(os.environ.get("PORT", 3055))

    async with websockets.serve(handler, "0.0.0.0", port):
        print(f"WebSocket server running on port {port}", flush=True)
        # Run forever
        await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    asyncio.run(main())
