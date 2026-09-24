"""
AeroGuard - FastAPI Production Server
Serves real-time streaming audio WebSocket (/ws/audio), judge incident simulation (/api/simulate-incident),
FAA Part 139 digital audit generation (/api/audit), and interactive airfield radar HUD (/static).
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from runway_engine import AirfieldGrid, IncursionAlert
from audio_streamer import AssemblyAIAudioStreamer, TranscriptTurn
from agent_orchestrator import AgentOrchestrator, AeroGuardState

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("aeroguard.server")

app = FastAPI(
    title="AeroGuard - Autonomous Airfield Ground Collision Interceptor",
    description="Real-Time Voice Agent Interceptor for Runway Safety (AssemblyAI Voice Agent Hackathon)",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Core domain singletons
airfield = AirfieldGrid()
connected_websockets: Set[WebSocket] = set()


async def broadcast_to_clients(message: dict):
    """Broadcasts a JSON event payload to all connected browser WebSockets."""
    if not connected_websockets:
        return
    text_data = json.dumps(message, default=lambda o: list(o) if isinstance(o, (set, tuple)) else str(o))
    dead_sockets = set()
    for ws in connected_websockets:
        try:
            await ws.send_text(text_data)
        except Exception:
            dead_sockets.add(ws)
    for dead in dead_sockets:
        connected_websockets.discard(dead)


orchestrator = AgentOrchestrator(airfield_grid=airfield, broadcast_callback=broadcast_to_clients)

# Mount static directory
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def get_index():
    """Serves the AeroGuard Dark-Mode Radar HUD UI."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"status": "AeroGuard Running", "ui": "static/index.html not yet generated"})


@app.get("/healthz")
async def health_check():
    """System health check verifying airfield grid, AssemblyAI settings, and active orchestrator state."""
    return {
        "status": "ok",
        "service": "AeroGuard Real-Time Voice Interceptor",
        "airport": settings.airport_icao,
        "sample_rate": settings.sample_rate,
        "audio_encoding": settings.audio_encoding,
        "assemblyai_key_configured": bool(settings.assemblyai_api_key and "mock" not in settings.assemblyai_api_key.lower()),
        "active_vehicles": len(airfield.vehicles),
        "state": orchestrator.current_state,
        "active_clients": len(connected_websockets)
    }


@app.get("/api/airfield")
async def get_airfield_state():
    """Returns full geospatial snapshot of runways, taxiways, vehicles, and clearances."""
    return airfield.get_airfield_snapshot()


@app.post("/api/reset")
async def reset_airfield():
    """Resets airfield state, clearances, and incident history to baseline."""
    global airfield, orchestrator
    airfield = AirfieldGrid()
    orchestrator = AgentOrchestrator(airfield_grid=airfield, broadcast_callback=broadcast_to_clients)
    await broadcast_to_clients({
        "type": "STATE_CHANGE",
        "to_state": AeroGuardState.IDLE,
        "details": {"action": "SYSTEM_RESET"}
    })
    await broadcast_to_clients({
        "type": "AIRFIELD_SNAPSHOT",
        "snapshot": airfield.get_airfield_snapshot()
    })
    return {"status": "reset_successful", "snapshot": airfield.get_airfield_snapshot()}


@app.post("/api/scenario/{scenario_id}")
async def switch_scenario(scenario_id: str):
    """Switches active airfield scenario between KORD_TUG_INCURSION and RJTT_HANEDA_2024."""
    global airfield, orchestrator
    if scenario_id not in ["KORD_TUG_INCURSION", "RJTT_HANEDA_2024"]:
        raise HTTPException(status_code=400, detail="Invalid scenario_id. Must be KORD_TUG_INCURSION or RJTT_HANEDA_2024")

    airfield.load_scenario(scenario_id)
    orchestrator = AgentOrchestrator(airfield_grid=airfield, broadcast_callback=broadcast_to_clients)
    
    await broadcast_to_clients({
        "type": "SCENARIO_CHANGED",
        "scenario_id": scenario_id,
        "airport": airfield.airport_icao,
        "snapshot": airfield.get_airfield_snapshot()
    })
    return {"status": "scenario_switched", "scenario_id": scenario_id, "airport": airfield.airport_icao, "snapshot": airfield.get_airfield_snapshot()}


@app.post("/api/simulate-readback-deviation")
async def simulate_readback_deviation():
    """
    Demonstrates the ICAO Readback Deviation & Discrepancy Engine:
    1. ATC issues clearance: "Tug 42, taxi via Echo, hold short of Runway 09L."
    2. Driver erroneously reads back: "Taxi via Echo, cross Runway 09L, Tug 42."
    3. AeroGuard detects critical clearance inversion in sub-300ms and fires immediate voice alert.
    """
    turn_atc = TranscriptTurn(
        transcript="Tug 42, taxi via Echo, hold short of Runway 09L.",
        end_of_turn=True,
        speaker="ATC_GROUND"
    )
    await orchestrator.handle_incoming_turn(turn_atc)
    await asyncio.sleep(0.3)

    turn_rb = TranscriptTurn(
        transcript="Taxi via Echo, cross Runway 09L, Tug 42.",
        end_of_turn=True,
        speaker="TUG_42_DRIVER"
    )
    await orchestrator.handle_incoming_turn(turn_rb)

    analysis = orchestrator.last_readback_analysis
    return {
        "status": "readback_test_executed",
        "deviation_detected": analysis is not None and not analysis.is_safe,
        "analysis": analysis.__dict__ if analysis else None,
        "barge_in_fired": orchestrator.barge_in_active
    }


@app.get("/api/audit")
async def get_faa_audit():
    """Generates and returns an FAA Part 139 / JCAB Digital Incident Audit Report."""
    audit_report = await orchestrator.generate_faa_part_139_audit()
    return audit_report


@app.post("/api/simulate-incident")
async def simulate_incident():
    """
    1-Click Judge Simulation Protocol:
    Demonstrates AeroGuard's sub-second autonomous voice interception without requiring a physical microphone.
    1. ATC issues initial clearance: "Tug 42, taxi via Echo, hold short of Runway 09L."
    2. Tug 42 acknowledges readback.
    3. Tug 42 starts taxiing South along Taxiway Echo.
    4. Tug 42 driver experiences high-noise distraction and breaches hold-short line towards active Runway 09L.
    5. Conflicting Flight AA104 is on 1.1 NM final approach.
    6. AeroGuard intercepts instantly, flushes audio buffers, triggers emergency barge-in alarm.
    7. Automated FAA Part 139 digital audit report is generated.
    """
    logger.info("Executing 1-Click Judge Incident Simulation Protocol...")

    # Step 1: Initial clearance broadcast
    turn1 = TranscriptTurn(
        transcript="Tug 42, taxi south via Echo, hold short of Runway 09L, traffic on final.",
        end_of_turn=True,
        speaker="ATC_GROUND"
    )
    await orchestrator.handle_incoming_turn(turn1)
    await asyncio.sleep(0.4)

    # Step 2: Tug readback
    turn2 = TranscriptTurn(
        transcript="Taxi south via Echo, hold short 09L, Tug 42.",
        end_of_turn=True,
        speaker="TUG_42_DRIVER"
    )
    await orchestrator.handle_incoming_turn(turn2)
    await asyncio.sleep(0.4)

    # Step 3: Vehicle approaches hold line (Y=350 to Y=330)
    airfield.update_vehicle_telemetry("TUG-42", x=500.0, y=345.0, speed_kts=14.0, heading_deg=180.0)
    await broadcast_to_clients({
        "type": "AIRFIELD_SNAPSHOT",
        "snapshot": airfield.get_airfield_snapshot()
    })
    await asyncio.sleep(0.3)

    # Step 4: Unauthorized Runway Safety Area (RSA) entry! (Y=325, inside Runway 09L zone)
    alert = airfield.update_vehicle_telemetry("TUG-42", x=500.0, y=320.0, speed_kts=12.0, heading_deg=180.0)
    await broadcast_to_clients({
        "type": "AIRFIELD_SNAPSHOT",
        "snapshot": airfield.get_airfield_snapshot()
    })

    # Step 5: Barge-in interruption
    await orchestrator.trigger_barge_in_interruption(alert)

    # Step 6: Generate post-incident FAA Part 139 audit
    audit = await orchestrator.generate_faa_part_139_audit()

    return {
        "status": "simulation_complete",
        "incident_detected": alert.severity == "CRITICAL_INCURSION",
        "barge_in_fired": orchestrator.barge_in_active,
        "alert": alert.__dict__,
        "audit_report": audit
    }


@app.websocket("/ws/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    """
    Real-Time Audio & Telemetry WebSocket Gateway.
    - Connects browser client audio stream to AssemblyAI v3 Streaming STT.
    - Streams live partial/final transcripts, airfield radar updates, and barge-in alarms.
    """
    await websocket.accept()
    connected_websockets.add(websocket)
    logger.info(f"Client connected to /ws/audio. Total active: {len(connected_websockets)}")

    # Initialize audio streamer for this connection
    streamer = AssemblyAIAudioStreamer(
        on_turn_callback=lambda turn: orchestrator.handle_incoming_turn(turn, audio_streamer=streamer)
    )
    await streamer.connect()

    # Send initial state and snapshot
    await websocket.send_text(json.dumps({
        "type": "INITIAL_HANDSHAKE",
        "airport": settings.airport_icao,
        "sample_rate": settings.sample_rate,
        "state": orchestrator.current_state,
        "snapshot": airfield.get_airfield_snapshot()
    }))

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"]:
                # Binary audio chunk (PCM 16kHz)
                raw_pcm = message["bytes"]
                await streamer.push_audio_chunk(raw_pcm)

            elif "text" in message and message["text"]:
                try:
                    payload = json.loads(message["text"])
                    msg_type = payload.get("type")

                    if msg_type == "TELEMETRY_UPDATE":
                        # Client sending updated vehicle position
                        veh_id = payload.get("vehicle_id", "TUG-42")
                        x = float(payload.get("x", 500.0))
                        y = float(payload.get("y", 400.0))
                        speed = float(payload.get("speed_kts", 12.0))
                        heading = float(payload.get("heading_deg", 180.0))
                        alert = airfield.update_vehicle_telemetry(veh_id, x, y, speed, heading)
                        if alert.severity == "CRITICAL_INCURSION":
                            await orchestrator.trigger_barge_in_interruption(alert, streamer)
                        elif alert.severity == "WARNING_HOLD_SHORT":
                            await orchestrator.transition_state(AeroGuardState.ALERT_BROADCAST, {"alert": alert.__dict__})

                        await broadcast_to_clients({
                            "type": "AIRFIELD_SNAPSHOT",
                            "snapshot": airfield.get_airfield_snapshot()
                        })

                    elif msg_type == "SPOKEN_SPEECH_INJECTION":
                        # Direct speech transcript injection for testing or simulation
                        text = payload.get("text", "")
                        speaker = payload.get("speaker", "RAMP_OPERATOR")
                        turn = TranscriptTurn(transcript=text, end_of_turn=True, speaker=speaker)
                        await orchestrator.handle_incoming_turn(turn, audio_streamer=streamer)

                    elif msg_type == "PING":
                        await websocket.send_text(json.dumps({"type": "PONG", "timestamp": time.time()}))

                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/audio.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        connected_websockets.discard(websocket)
        await streamer.close()
