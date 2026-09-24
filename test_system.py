"""
AeroGuard - Integration & System Test Suite
Covers:
1. Health and diagnostic endpoint checks (/healthz).
2. Airfield coordinate geometry, zones, and clearance parsing.
3. Real-time runway incursion conflict detection (unauthorized crossing vs. cleared crossing).
4. Audio streamer AssemblyAI v3 turn message schema parsing and barge-in buffer flushing.
5. Agent orchestrator audio lifecycle state transitions (LISTENING -> VALIDATING -> ALERT_BROADCAST -> INTERRUPTED).
6. 1-Click Judge Incident Simulation endpoint (/api/simulate-incident).
7. FAA Part 139 Digital Audit generation (regulatory schema compliance).
8. Real-time WebSocket connection, ping-pong, and live telemetry injection (/ws/audio).
"""

import json
import pytest
from starlette.testclient import TestClient

from config import settings
from server import app, airfield, orchestrator
from runway_engine import AirfieldGrid, ClearanceState, IncursionAlert
from audio_streamer import AssemblyAIAudioStreamer, TranscriptTurn
from agent_orchestrator import AgentOrchestrator, AeroGuardState


@pytest.fixture
def client():
    """Returns a TestClient instance for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def fresh_airfield():
    """Returns a fresh AirfieldGrid instance for isolated unit tests."""
    return AirfieldGrid()


# =====================================================================
# 1. HEALTHCHECK TESTS
# =====================================================================

def test_healthz_endpoint(client):
    """Verifies that /healthz returns status: ok and diagnostic parameters."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["airport"] == settings.airport_icao
    assert data["sample_rate"] == 16000
    assert "active_vehicles" in data
    assert "state" in data


# =====================================================================
# 2. RUNWAY ENGINE & CLEARANCE PARSING TESTS
# =====================================================================

def test_clearance_spoken_parsing(fresh_airfield):
    """Verifies parsing of spoken ATC instructions into clearance states."""
    # Test hold short parsing
    state = fresh_airfield.parse_spoken_clearance("Tug 42, taxi via Echo, hold short of Runway 09L", vehicle_id="TUG-42")
    assert "09L" in state.hold_short_runways
    assert "09L" not in state.cleared_runway_crossings

    # Test crossing clearance parsing
    state2 = fresh_airfield.parse_spoken_clearance("Tug 42, cleared to cross Runway 09L at Echo, expedite", vehicle_id="TUG-42")
    assert "09L" in state2.cleared_runway_crossings
    assert "09L" not in state2.hold_short_runways

    # Test emergency immediate stop command
    state3 = fresh_airfield.parse_spoken_clearance("Tug 42, immediate stop! Cancel crossing clearance!", vehicle_id="TUG-42")
    assert len(state3.cleared_runway_crossings) == 0
    assert "09L" in state3.hold_short_runways


def test_runway_incursion_conflict_detection(fresh_airfield):
    """
    Critical Test: Verifies that an incursion warning triggers when a tug
    enters Runway 09L without clearance while aircraft is on final.
    """
    # Tug has NO crossing clearance (only hold short)
    fresh_airfield.clearances["TUG-42"].cleared_runway_crossings.clear()
    fresh_airfield.clearances["TUG-42"].hold_short_runways.add("09L")

    # Move Tug-42 directly into Runway 09L Safety Area (Y=320, inside RSA [265, 335])
    alert = fresh_airfield.update_vehicle_telemetry(
        vehicle_id="TUG-42",
        x=500.0,
        y=320.0,
        speed_kts=12.0,
        heading_deg=180.0
    )

    assert alert.severity == "CRITICAL_INCURSION"
    assert alert.runway_id == "09L"
    assert alert.barge_in_required is True
    assert alert.conflicting_traffic == "AA104"
    assert alert.time_to_impact_sec is not None
    assert alert.time_to_impact_sec > 0


def test_authorized_crossing_no_incursion(fresh_airfield):
    """Verifies that when crossing clearance is granted, entering Runway 09L does NOT trigger incursion."""
    fresh_airfield.clearances["TUG-42"].cleared_runway_crossings.add("09L")
    fresh_airfield.clearances["TUG-42"].hold_short_runways.discard("09L")

    alert = fresh_airfield.update_vehicle_telemetry(
        vehicle_id="TUG-42",
        x=500.0,
        y=300.0,  # Runway centerline
        speed_kts=12.0,
        heading_deg=180.0
    )

    assert alert.severity == "NORMAL"
    assert alert.barge_in_required is False


# =====================================================================
# 3. AUDIO STREAMER & ASSEMBLYAI PROTOCOL TESTS
# =====================================================================

def test_assemblyai_v3_turn_schema_parsing():
    """Verifies that AssemblyAI v3 Turn messages are correctly parsed into TranscriptTurn."""
    streamer = AssemblyAIAudioStreamer()

    # Simulated AssemblyAI v3 partial turn message
    v3_partial_msg = {
        "message_type": "Turn",
        "transcript": "Tug 42 taxi south via Echo",
        "end_of_turn": False,
        "words": [{"text": "Tug", "start": 100, "end": 200}]
    }
    turn_partial = streamer._parse_assemblyai_message(v3_partial_msg)
    assert turn_partial is not None
    assert turn_partial.transcript == "Tug 42 taxi south via Echo"
    assert turn_partial.end_of_turn is False
    assert turn_partial.source == "AssemblyAI_v3_Streaming"

    # Simulated AssemblyAI v3 final turn message
    v3_final_msg = {
        "message_type": "Turn",
        "transcript": "Tug 42 taxi south via Echo, hold short of Runway 09L.",
        "end_of_turn": True,
        "words": []
    }
    turn_final = streamer._parse_assemblyai_message(v3_final_msg)
    assert turn_final is not None
    assert turn_final.transcript == "Tug 42 taxi south via Echo, hold short of Runway 09L."
    assert turn_final.end_of_turn is True


@pytest.mark.asyncio
async def test_barge_in_buffer_flushing():
    """Verifies that barge-in buffer flushing clears pending audio queue."""
    streamer = AssemblyAIAudioStreamer()
    streamer.is_running = True

    # Push 5 audio chunks
    for i in range(5):
        await streamer.push_audio_chunk(b"dummy_pcm_chunk")
    assert streamer.audio_queue.qsize() == 5

    # Trigger flush
    await streamer.flush_buffer()
    assert streamer.audio_queue.qsize() == 0


# =====================================================================
# 4. ORCHESTRATOR & BARGE-IN INTERRUPT TESTS
# =====================================================================

@pytest.mark.asyncio
async def test_agent_orchestrator_state_transitions():
    """Verifies state machine transitions during turn processing and incursion triggers."""
    grid = AirfieldGrid()
    orch = AgentOrchestrator(airfield_grid=grid)

    assert orch.current_state == AeroGuardState.IDLE

    # Partial turn should transition to LISTENING
    partial_turn = TranscriptTurn(transcript="Tug 42 holding short", end_of_turn=False)
    await orch.handle_incoming_turn(partial_turn)
    assert orch.current_state == AeroGuardState.LISTENING

    # Final turn with compliant instruction
    final_turn = TranscriptTurn(transcript="Tug 42, taxi via Echo, hold short 09L", end_of_turn=True)
    await orch.handle_incoming_turn(final_turn)
    # Since position is safe, transitions through VALIDATING and back to LISTENING
    assert orch.current_state == AeroGuardState.LISTENING

    # Now move vehicle into runway without clearance and trigger incursion
    alert = grid.update_vehicle_telemetry("TUG-42", x=500.0, y=310.0)
    assert alert.severity == "CRITICAL_INCURSION"

    await orch.trigger_barge_in_interruption(alert)
    assert orch.barge_in_active is True
    assert orch.current_state == AeroGuardState.INTERRUPTED
    assert len(orch.incident_log) > 0


# =====================================================================
# 5. JUDGE SIMULATION ENDPOINT TEST
# =====================================================================

def test_judge_simulation_endpoint(client):
    """
    Critical Hackathon Test:
    Verifies that POST /api/simulate-incident executes the complete end-to-end scenario:
    - Pre-recorded ATC instruction and Tug readback
    - Vehicle telemetry movement towards 09L
    - Sub-second incursion detection and barge-in override
    - Structured FAA Part 139 digital audit generation
    """
    response = client.post("/api/simulate-incident")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "simulation_complete"
    assert data["incident_detected"] is True
    assert data["barge_in_fired"] is True
    assert "alert" in data
    assert data["alert"]["runway_id"] == "09L"
    assert "audit_report" in data
    assert data["audit_report"]["airport_icao"] == "KORD"
    assert "regulatory_violations" in data["audit_report"]


# =====================================================================
# 6. FAA PART 139 AUDIT STRUCTURE TEST
# =====================================================================

def test_faa_audit_endpoint(client):
    """Verifies that GET /api/audit returns a compliant FAA Part 139 schema."""
    response = client.get("/api/audit")
    assert response.status_code == 200
    data = response.json()

    required_keys = [
        "report_id", "airport_icao", "incident_date", "severity_category",
        "involved_entities", "incident_chronology", "root_cause_analysis",
        "regulatory_violations", "preventative_action_plan", "aeroguard_system_effectiveness"
    ]
    for key in required_keys:
        assert key in data, f"Missing required key in FAA audit: {key}"

    assert data["airport_icao"] == "KORD"
    assert "Tug" in str(data["involved_entities"])
    assert len(data["regulatory_violations"]) > 0


# =====================================================================
# 7. WEBSOCKET GATEWAY & LIVE TELEMETRY TEST
# =====================================================================

def test_websocket_audio_gateway(client):
    """Verifies WebSocket handshake, PING/PONG, and telemetry event processing."""
    with client.websocket_connect("/ws/audio") as websocket:
        # Initial handshake message
        handshake = websocket.receive_json()
        assert handshake["type"] == "INITIAL_HANDSHAKE"
        assert handshake["airport"] == "KORD"
        assert "snapshot" in handshake

        # Send PING message
        websocket.send_json({"type": "PING"})
        pong = websocket.receive_json()
        assert pong["type"] == "PONG"

        # Send Telemetry Update (Safe coordinates)
        websocket.send_json({
            "type": "TELEMETRY_UPDATE",
            "vehicle_id": "TUG-42",
            "x": 500.0,
            "y": 420.0,
            "speed_kts": 10.0,
            "heading_deg": 180.0
        })
        snap_resp = websocket.receive_json()
        assert snap_resp["type"] == "AIRFIELD_SNAPSHOT"


# =====================================================================
# 8. ICAO READBACK DEVIATION ENGINE TESTS
# =====================================================================

def test_readback_deviation_detection(fresh_airfield):
    """Verifies that the ICAO Readback Deviation Engine detects omissions and inversions."""
    # Test 1: Compliant readback match
    atc = "Tug 42, taxi via Echo, hold short of Runway 09L."
    rb_match = "Taxi via Echo, hold short 09L, Tug 42."
    res1 = fresh_airfield.analyze_readback_deviation(atc, rb_match)
    assert res1.is_safe is True
    assert res1.status == "MATCH"

    # Test 2: Omission of hold short
    rb_omission = "Taxi via Echo, Tug 42."
    res2 = fresh_airfield.analyze_readback_deviation(atc, rb_omission)
    assert res2.is_safe is False
    assert res2.status == "DEVIATION_DETECTED"
    assert any("HOLD_SHORT_OMISSION" in dev for dev in res2.deviations)

    # Test 3: Critical clearance inversion (ATC says hold short, driver says cross)
    rb_inversion = "Taxi via Echo, cleared to cross 09L, Tug 42."
    res3 = fresh_airfield.analyze_readback_deviation(atc, rb_inversion)
    assert res3.is_safe is False
    assert res3.status == "CRITICAL_INVERSION"


# =====================================================================
# 9. MULTI-AIRFIELD SCENARIO (TOKYO HANEDA RJTT) TESTS
# =====================================================================

def test_tokyo_haneda_scenario_switch(client):
    """Verifies switching from KORD to RJTT Haneda 2024 Historic Incident."""
    resp = client.post("/api/scenario/RJTT_HANEDA_2024")
    assert resp.status_code == 200
    data = resp.json()
    assert data["airport"] == "RJTT"
    assert "JA722A" in data["snapshot"]["vehicles"]
    assert "JAL516" in data["snapshot"]["traffic"]
    assert data["snapshot"]["runway_safety_areas"][0]["id"] == "34R"

    # Switch back to KORD
    resp2 = client.post("/api/scenario/KORD_TUG_INCURSION")
    assert resp2.status_code == 200
    assert resp2.json()["airport"] == "KORD"


def test_simulate_readback_deviation_endpoint(client):
    """Verifies the /api/simulate-readback-deviation endpoint triggers discrepancy detection."""
    resp = client.post("/api/simulate-readback-deviation")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "readback_test_executed"
    assert data["deviation_detected"] is True
    assert data["analysis"]["status"] == "CRITICAL_INVERSION"

