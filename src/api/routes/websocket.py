"""
WebSocket routes
Provides real-time communication functionality
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Set


router = APIRouter()

# Global WebSocket connection manager
active_connections: Set[WebSocket] = set()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):
    """WebSocket endpoint"""
    # Accept connection
    await websocket.accept()
    active_connections.add(websocket)

    try:
        # Keep connection alive and receive messages
        while True:
            # Receive client messages (if any)
            data = await websocket.receive_text()
            # Client messages can be processed here.
            # Currently used primarily for server-side push, so no processing for now.
    except WebSocketDisconnect:
        active_connections.remove(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)


async def broadcast_message(message_type: str, data: dict):
    """Broadcast a message to all connected clients"""
    message = {
        "type": message_type,
        "data": data
    }

    # Collect disconnected connections
    disconnected = set()

    for connection in active_connections:
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.add(connection)

    # Clean up disconnected connections
    for connection in disconnected:
        active_connections.discard(connection)
