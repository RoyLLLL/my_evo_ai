"""
WebSocket v2 routes for SuperAgent with human-in-the-loop support.

Protocol:
  Client -> Server:
    1. {"type": "authorization", "token": "...", "api_key": "..."}
    2. {"type": "message", "message": "...", "files": [...]}

  Server -> Client:
    {"type": "agent_message", "content": "...", "author": "...", "state": {...}}
    {"type": "interrupt_received", "content": "...", "state": {...}}
    {"type": "turn_complete", "state": {...}}
    {"type": "error", "content": "..."}
    {"type": "state_snapshot", "state": {...}}
"""

import json
import logging
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.core.jwt_middleware import get_jwt_token_ws, verify_user_client
from src.services import agent_service
from src.services.adk.agent_runner import create_super_agent_session
from src.services.service_providers import (
    session_service,
    artifacts_service,
    memory_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/super-chat",
    tags=["super-chat"],
    responses={404: {"description": "Not found"}},
)


@router.websocket("/ws/{agent_id}/{external_id}")
async def websocket_super_chat(
    websocket: WebSocket,
    agent_id: str,
    external_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for SuperAgent with human-in-the-loop.

    The agent stays alive across turns. Users can send messages mid-execution
    to update todos, provide clarifications, or redirect the agent.
    State (todos, research) is streamed back with every agent response.
    """
    exit_stack = None
    try:
        await websocket.accept()
        logger.info("SuperAgent WebSocket accepted, waiting for auth")

        # --- Authentication (same pattern as v1) ---
        try:
            auth_data = await websocket.receive_json()

            if not (
                auth_data.get("type") == "authorization"
                and (auth_data.get("token") or auth_data.get("api_key"))
            ):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            agent = agent_service.get_agent(db, agent_id)
            if not agent:
                await websocket.send_json({"type": "error", "content": "Agent not found"})
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            if agent.type != "super":
                await websocket.send_json({
                    "type": "error",
                    "content": f"Agent type '{agent.type}' not supported. Use a 'super' agent.",
                })
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            is_authenticated = False

            if auth_data.get("token"):
                try:
                    payload = await get_jwt_token_ws(auth_data["token"])
                    if payload:
                        await verify_user_client(payload, db, agent.client_id)
                        is_authenticated = True
                except Exception as e:
                    logger.warning(f"JWT auth failed: {e}")

            if not is_authenticated and auth_data.get("api_key"):
                if agent.config and agent.config.get("api_key") == auth_data.get("api_key"):
                    is_authenticated = True

            if not is_authenticated:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

        except (WebSocketDisconnect, json.JSONDecodeError):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # --- Create persistent SuperAgentSession ---
        try:
            super_session, exit_stack = await create_super_agent_session(
                agent_id=agent_id,
                external_id=external_id,
                session_service=session_service,
                artifacts_service=artifacts_service,
                memory_service=memory_service,
                db=db,
            )
        except Exception as e:
            logger.error(f"Failed to create SuperAgentSession: {e}")
            await websocket.send_json({"type": "error", "content": str(e)})
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return

        # Send initial state snapshot
        initial_state = super_session.event_bus.build_state(super_session.session_id)
        await websocket.send_json({
            "type": "state_snapshot",
            "state": initial_state,
            "session_id": super_session.session_id,
        })

        logger.info(f"SuperAgent session ready: {super_session.session_id}")

        # --- Main message loop ---
        while True:
            try:
                data = await websocket.receive_json()
                msg_type = data.get("type", "message")
                message = data.get("message", "")

                if not message:
                    continue

                # If agent is currently processing, treat as interrupt
                if super_session.is_processing:
                    logger.info(f"Interrupt received: {message[:50]}...")
                    await super_session.send_interrupt(message)
                    await websocket.send_json({
                        "type": "interrupt_acknowledged",
                        "content": message,
                    })
                    continue

                # Normal turn - stream agent responses
                async for event in super_session.run_turn(message):
                    await websocket.send_json(event)

            except WebSocketDisconnect:
                logger.info(f"Client disconnected: {super_session.session_id}")
                break
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received")
                continue
            except Exception as e:
                logger.error(f"Error in message loop: {e}", exc_info=True)
                try:
                    await websocket.send_json({"type": "error", "content": str(e)})
                except Exception:
                    break

    except Exception as e:
        logger.error(f"SuperAgent WebSocket error: {e}", exc_info=True)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass
    finally:
        if exit_stack:
            try:
                await exit_stack.aclose()
            except Exception as e:
                logger.error(f"Error closing exit stack: {e}")
