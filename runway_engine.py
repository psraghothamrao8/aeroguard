"""
AeroGuard - Runway Engine & Airfield Coordinate State Machine (Upgraded v2)
Maintains active airport vector grid (KORD Chicago O'Hare and RJTT Tokyo Haneda),
tracks ramp vehicles and inbound aircraft, parses ATC clearances, detects unauthorized
runway incursions, and executes real-time ICAO readback deviation analysis.
"""

from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
import math
import re
import time
import logging

logger = logging.getLogger("aeroguard.runway_engine")


@dataclass
class AirfieldZone:
    name: str
    zone_type: str  # "RUNWAY", "TAXIWAY", "RAMP", "HOLD_LINE"
    bounds: Tuple[float, float, float, float]  # (x_min, y_min, x_max, y_max)
    active_runway_id: Optional[str] = None
    crossing_points: List[str] = field(default_factory=list)


@dataclass
class AircraftTraffic:
    callsign: str
    aircraft_type: str
    assigned_runway: str
    status: str  # "FINAL_APPROACH", "ROLLOUT", "LINE_UP_WAIT", "DEPARTING"
    distance_nm: float
    ground_speed_kts: float
    x: float
    y: float


@dataclass
class VehicleTelemetry:
    vehicle_id: str
    vehicle_type: str  # "PUSHBACK_TUG", "BAGGAGE_CART", "COAST_GUARD_AIRCRAFT", "FOLLOW_ME"
    x: float
    y: float
    speed_kts: float
    heading_deg: float
    towed_aircraft: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ClearanceState:
    vehicle_id: str
    cleared_taxiways: Set[str] = field(default_factory=set)
    hold_short_runways: Set[str] = field(default_factory=set)
    cleared_runway_crossings: Set[str] = field(default_factory=set)
    target_destination: Optional[str] = None
    last_instruction_text: str = ""
    last_update_ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "cleared_taxiways": list(self.cleared_taxiways),
            "hold_short_runways": list(self.hold_short_runways),
            "cleared_runway_crossings": list(self.cleared_runway_crossings),
            "target_destination": self.target_destination,
            "last_instruction_text": self.last_instruction_text,
            "last_update_ts": self.last_update_ts
        }


@dataclass
class IncursionAlert:
    alert_id: str
    severity: str  # "CRITICAL_INCURSION", "WARNING_HOLD_SHORT", "READBACK_DEVIATION", "NORMAL"
    vehicle_id: str
    runway_id: str
    message: str
    conflicting_traffic: Optional[str] = None
    time_to_impact_sec: Optional[float] = None
    barge_in_required: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class ReadbackAnalysis:
    status: str  # "MATCH", "DEVIATION_DETECTED", "CRITICAL_INVERSION"
    is_safe: bool
    atc_instruction: str
    readback: str
    deviations: List[str] = field(default_factory=list)
    alert_directive: Optional[str] = None


class AirfieldGrid:
    """
    Multi-Scenario Airfield Grid Engine:
    - KORD: Chicago O'Hare International (Runway 09L/27R, Taxiway Echo & Alpha, Tug-42 vs AA104)
    - RJTT: Tokyo Haneda International (Runway 34R, Taxiway C5 Hold Point, Coast Guard JA722A vs JAL516 A350)
    """

    def __init__(self, scenario_id: str = "KORD_TUG_INCURSION"):
        self.scenario_id = scenario_id
        self.airport_icao = "KORD" if "KORD" in scenario_id else "RJTT"
        self.zones: Dict[str, AirfieldZone] = {}
        self.clearances: Dict[str, ClearanceState] = {}
        self.vehicles: Dict[str, VehicleTelemetry] = {}
        self.active_traffic: Dict[str, AircraftTraffic] = {}
        self.last_atc_instruction: Optional[str] = None
        self.load_scenario(scenario_id)

    def load_scenario(self, scenario_id: str):
        """Loads specific airfield geometry and traffic vectors."""
        self.scenario_id = scenario_id
        self.zones.clear()
        self.clearances.clear()
        self.vehicles.clear()
        self.active_traffic.clear()

        if scenario_id == "RJTT_HANEDA_2024":
            self.airport_icao = "RJTT"
            self._init_haneda_scenario()
        else:
            self.airport_icao = "KORD"
            self._init_ohare_scenario()

    def _init_ohare_scenario(self):
        """KORD Chicago O'Hare: Tug-42 towing Boeing 777, AA104 on 09L final approach."""
        self.zones["RUNWAY_PRIMARY"] = AirfieldZone(
            name="Runway 09L/27R",
            zone_type="RUNWAY",
            bounds=(80.0, 265.0, 920.0, 335.0),
            active_runway_id="09L",
            crossing_points=["Echo", "Foxtrot"]
        )
        self.zones["HOLD_SHORT_NORTH"] = AirfieldZone(
            name="Hold Short Line 09L North (Taxiway Echo)",
            zone_type="HOLD_LINE",
            bounds=(470.0, 336.0, 530.0, 360.0),
            active_runway_id="09L"
        )
        self.zones["TAXIWAY_ALPHA"] = AirfieldZone(
            name="Taxiway Alpha",
            zone_type="TAXIWAY",
            bounds=(50.0, 400.0, 950.0, 440.0)
        )
        self.zones["TAXIWAY_CONNECTOR"] = AirfieldZone(
            name="Taxiway Echo",
            zone_type="TAXIWAY",
            bounds=(480.0, 150.0, 520.0, 450.0),
            crossing_points=["09L"]
        )

        self.vehicles["TUG-42"] = VehicleTelemetry(
            vehicle_id="TUG-42",
            vehicle_type="PUSHBACK_TUG",
            x=500.0,
            y=430.0,
            speed_kts=12.0,
            heading_deg=180.0,
            towed_aircraft="Boeing 777-200ER (AAL88)"
        )

        self.clearances["TUG-42"] = ClearanceState(
            vehicle_id="TUG-42",
            cleared_taxiways={"Alpha", "Echo"},
            hold_short_runways={"09L"},
            cleared_runway_crossings=set(),
            target_destination="South Maintenance Hangars",
            last_instruction_text="Tug 42, taxi via Alpha, south on Echo, hold short of Runway 09L"
        )

        self.active_traffic["AA104"] = AircraftTraffic(
            callsign="AA104",
            aircraft_type="Boeing 737-800",
            assigned_runway="09L",
            status="FINAL_APPROACH",
            distance_nm=1.2,
            ground_speed_kts=138.0,
            x=180.0,
            y=300.0
        )
        self.last_atc_instruction = "Tug 42, taxi south via Echo, hold short of Runway 09L, traffic on final."

    def _init_haneda_scenario(self):
        """Tokyo Haneda RJTT 2024 Historic Scenario: JA722A Dash-8 vs JAL516 A350 on Runway 34R."""
        self.zones["RUNWAY_PRIMARY"] = AirfieldZone(
            name="Runway 16L/34R",
            zone_type="RUNWAY",
            bounds=(80.0, 265.0, 920.0, 335.0),
            active_runway_id="34R",
            crossing_points=["C5", "C6"]
        )
        self.zones["HOLD_SHORT_NORTH"] = AirfieldZone(
            name="Holding Point C5 (Runway 34R)",
            zone_type="HOLD_LINE",
            bounds=(470.0, 336.0, 530.0, 360.0),
            active_runway_id="34R"
        )
        self.zones["TAXIWAY_ALPHA"] = AirfieldZone(
            name="Taxiway Charlie",
            zone_type="TAXIWAY",
            bounds=(50.0, 400.0, 950.0, 440.0)
        )
        self.zones["TAXIWAY_CONNECTOR"] = AirfieldZone(
            name="Taxiway C5",
            zone_type="TAXIWAY",
            bounds=(480.0, 150.0, 520.0, 450.0),
            crossing_points=["34R"]
        )

        self.vehicles["JA722A"] = VehicleTelemetry(
            vehicle_id="JA722A",
            vehicle_type="COAST_GUARD_AIRCRAFT",
            x=500.0,
            y=430.0,
            speed_kts=15.0,
            heading_deg=180.0,
            towed_aircraft="De Havilland Dash-8 Q300"
        )

        self.clearances["JA722A"] = ClearanceState(
            vehicle_id="JA722A",
            cleared_taxiways={"Charlie", "C5"},
            hold_short_runways={"34R"},
            cleared_runway_crossings=set(),
            target_destination="Holding Point C5",
            last_instruction_text="JA722A, Tokyo Tower, taxi to holding point C5, hold short of Runway 34R, number 1."
        )

        self.active_traffic["JAL516"] = AircraftTraffic(
            callsign="JAL516",
            aircraft_type="Airbus A350-900",
            assigned_runway="34R",
            status="FINAL_APPROACH",
            distance_nm=1.1,
            ground_speed_kts=142.0,
            x=180.0,
            y=300.0
        )
        self.last_atc_instruction = "JA722A, taxi to holding point C5, hold short Runway 34R, number 1."

    def analyze_readback_deviation(self, atc_text: str, readback_text: str) -> ReadbackAnalysis:
        """
        ICAO Readback Deviation & Discrepancy Engine:
        Compares ATC instruction against driver/pilot readback in real time.
        Detects:
        1. Omission of 'HOLD SHORT' restriction.
        2. Clearance Inversion (ATC instructed Hold Short, readback claimed Line Up / Cross).
        3. Runway Designator Mismatch (e.g. 09L vs 27R).
        """
        atc_clean = atc_text.upper()
        rb_clean = readback_text.upper()
        deviations = []

        atc_has_hold_short = "HOLD SHORT" in atc_clean or "HOLDING POINT" in atc_clean
        rb_has_hold_short = "HOLD SHORT" in rb_clean or "HOLDING POINT" in rb_clean

        atc_runways = set(re.findall(r"(?:RUNWAY\s+)?(\d{1,2}[LRC]?)", atc_clean))
        rb_runways = set(re.findall(r"(?:RUNWAY\s+)?(\d{1,2}[LRC]?)", rb_clean))

        # Check 1: Inversion - ATC said hold short, readback claimed crossing / line up
        if atc_has_hold_short:
            if any(term in rb_clean for term in ["CROSS", "CLEARED TO CROSS", "LINE UP", "TAKE OFF", "CROSSING"]):
                deviations.append("CRITICAL_CLEARANCE_INVERSION: ATC instructed HOLD SHORT, but readback stated CROSS/LINE UP.")
                return ReadbackAnalysis(
                    status="CRITICAL_INVERSION",
                    is_safe=False,
                    atc_instruction=atc_text,
                    readback=readback_text,
                    deviations=deviations,
                    alert_directive="READBACK ERROR! HOLD SHORT REQUIRED! CROSSING NOT PERMITTED!"
                )

        # Check 2: Omission of Hold Short
        if atc_has_hold_short and not rb_has_hold_short:
            deviations.append("HOLD_SHORT_OMISSION: Pilot/driver readback omitted mandatory HOLD SHORT restriction.")

        # Check 3: Runway Mismatch
        if atc_runways and rb_runways:
            if not atc_runways.intersection(rb_runways):
                deviations.append(f"RUNWAY_MISMATCH: ATC referenced {atc_runways}, but readback referenced {rb_runways}.")

        if deviations:
            return ReadbackAnalysis(
                status="DEVIATION_DETECTED",
                is_safe=False,
                atc_instruction=atc_text,
                readback=readback_text,
                deviations=deviations,
                alert_directive="READBACK DISCREPANCY DETECTED! VERIFY HOLD SHORT RESTRICTION WITH ATC!"
            )

        return ReadbackAnalysis(
            status="MATCH",
            is_safe=True,
            atc_instruction=atc_text,
            readback=readback_text,
            deviations=[],
            alert_directive=None
        )

    def parse_spoken_clearance(self, text: str, vehicle_id: Optional[str] = None) -> ClearanceState:
        """Parses spoken ATC clearance or readback to update the vehicle's clearance matrix."""
        veh_id = vehicle_id or list(self.vehicles.keys())[0]
        clean_text = text.upper()
        state = self.clearances.get(veh_id, ClearanceState(vehicle_id=veh_id))
        state.last_instruction_text = text
        state.last_update_ts = time.time()

        rwy_id = self.zones["RUNWAY_PRIMARY"].active_runway_id or "09L"

        # Check for immediate hold / cancel
        if any(w in clean_text for w in ["IMMEDIATE STOP", "HOLD POSITION", "CANCEL CROSSING", "STOP STOP STOP"]):
            state.cleared_runway_crossings.clear()
            state.hold_short_runways.add(rwy_id)
            logger.warning(f"EMERGENCY HOLD command detected for {veh_id}: {text}")
            self.clearances[veh_id] = state
            return state

        # Check for crossing clearances
        crossing_matches = re.findall(r"(?:CLEARED TO CROSS|CROSS|CROSSING APPROVED)\s+(?:RUNWAY\s+)?(\d{1,2}[LRC]?)", clean_text)
        for rwy in crossing_matches:
            normalized_rwy = rwy.strip()
            state.cleared_runway_crossings.add(normalized_rwy)
            state.hold_short_runways.discard(normalized_rwy)
            logger.info(f"Clearance granted: {veh_id} cleared to cross Runway {normalized_rwy}")

        # Check for hold short instructions
        hold_matches = re.findall(r"HOLD SHORT(?:\s+OF)?(?:\s+RUNWAY)?\s+(\d{1,2}[LRC]?)", clean_text)
        for rwy in hold_matches:
            normalized_rwy = rwy.strip()
            state.hold_short_runways.add(normalized_rwy)
            state.cleared_runway_crossings.discard(normalized_rwy)
            logger.info(f"Clearance updated: {veh_id} MUST HOLD SHORT of Runway {normalized_rwy}")

        self.clearances[veh_id] = state
        return state

    def update_vehicle_telemetry(
        self,
        vehicle_id: Optional[str] = None,
        x: float = 500.0,
        y: float = 430.0,
        speed_kts: float = 12.0,
        heading_deg: float = 180.0
    ) -> IncursionAlert:
        """Updates coordinate telemetry for ground vehicle and performs real-time spatial conflict check."""
        veh_id = vehicle_id or list(self.vehicles.keys())[0]
        v_current = self.vehicles.get(veh_id)
        v_type = v_current.vehicle_type if v_current else "TUG"
        towed = v_current.towed_aircraft if v_current else "Boeing 777"

        self.vehicles[veh_id] = VehicleTelemetry(
            vehicle_id=veh_id,
            vehicle_type=v_type,
            x=x,
            y=y,
            speed_kts=speed_kts,
            heading_deg=heading_deg,
            towed_aircraft=towed,
            timestamp=time.time()
        )

        return self.evaluate_incursion_risk(veh_id)

    def evaluate_incursion_risk(self, vehicle_id: Optional[str] = None) -> IncursionAlert:
        """Evaluates immediate spatial collision / runway incursion risk."""
        veh_id = vehicle_id or list(self.vehicles.keys())[0]
        veh = self.vehicles.get(veh_id)
        if not veh:
            return IncursionAlert(
                alert_id=f"ALT-NOM-{int(time.time()*1000)}",
                severity="NORMAL",
                vehicle_id=veh_id,
                runway_id="NONE",
                message="Vehicle not registered in telemetry grid."
            )

        clearance = self.clearances.get(veh_id, ClearanceState(vehicle_id=veh_id))
        rwy_zone = self.zones["RUNWAY_PRIMARY"]
        rwy_id = rwy_zone.active_runway_id or "09L"
        rwy_x_min, rwy_y_min, rwy_x_max, rwy_y_max = rwy_zone.bounds

        # Check if vehicle has breached the Runway Safety Area (RSA)
        in_runway_box = (rwy_x_min <= veh.x <= rwy_x_max) and (rwy_y_min <= veh.y <= rwy_y_max)
        # Check if approaching hold line (between Y=336 and Y=355 heading South)
        approaching_north_hold = (470.0 <= veh.x <= 530.0) and (335.0 < veh.y <= 355.0) and (150.0 <= veh.heading_deg <= 210.0)

        # Active conflicting traffic check
        conflicting_flight = list(self.active_traffic.values())[0] if self.active_traffic else None
        traffic_info = f"{conflicting_flight.callsign} ({conflicting_flight.aircraft_type}) on {rwy_id} final ({conflicting_flight.distance_nm} NM)" if conflicting_flight else f"Active Runway {rwy_id} Traffic"

        # Case 1: Direct Runway Incursion! (Inside RSA without clearance)
        if in_runway_box:
            is_cleared = rwy_id in clearance.cleared_runway_crossings
            if not is_cleared:
                time_to_impact = round((conflicting_flight.distance_nm * 3600.0) / max(conflicting_flight.ground_speed_kts, 100.0), 1) if conflicting_flight else 14.0
                return IncursionAlert(
                    alert_id=f"ALT-CRIT-{int(time.time()*1000)}",
                    severity="CRITICAL_INCURSION",
                    vehicle_id=veh_id,
                    runway_id=rwy_id,
                    message=f"CRITICAL RUNWAY INCURSION: {veh_id} ENTERED RUNWAY {rwy_id} WITHOUT CLEARANCE! CONFLICTING TRAFFIC: {traffic_info}. STOP IMMEDIATELY!",
                    conflicting_traffic=conflicting_flight.callsign if conflicting_flight else "AA104",
                    time_to_impact_sec=time_to_impact,
                    barge_in_required=True
                )

        # Case 2: Approaching Hold Short Line at speed with no crossing clearance
        if approaching_north_hold and rwy_id not in clearance.cleared_runway_crossings:
            if veh.speed_kts > 5.0:
                return IncursionAlert(
                    alert_id=f"ALT-WARN-{int(time.time()*1000)}",
                    severity="WARNING_HOLD_SHORT",
                    vehicle_id=veh_id,
                    runway_id=rwy_id,
                    message=f"CAUTION: {veh_id} APPROACHING RUNWAY {rwy_id} HOLD LINE AT {veh.speed_kts} KTS. NO CROSSING CLEARANCE RECEIVED.",
                    conflicting_traffic=conflicting_flight.callsign if conflicting_flight else "AA104",
                    time_to_impact_sec=28.0,
                    barge_in_required=False
                )

        # Case 3: Nominal / Compliant operation
        return IncursionAlert(
            alert_id=f"ALT-OK-{int(time.time()*1000)}",
            severity="NORMAL",
            vehicle_id=veh_id,
            runway_id=rwy_id,
            message=f"{veh_id} operating nominally on cleared routing. Runway {rwy_id} clear.",
            barge_in_required=False
        )

    def get_airfield_snapshot(self) -> dict:
        """Returns complete serializable snapshot of the airport grid for HUD rendering."""
        primary_rwy = self.zones.get("RUNWAY_PRIMARY", AirfieldZone("Runway", "RUNWAY", (80, 265, 920, 335), "09L"))
        return {
            "timestamp": time.time(),
            "scenario_id": self.scenario_id,
            "airport_icao": self.airport_icao,
            "vehicles": {vid: v.__dict__ for vid, v in self.vehicles.items()},
            "traffic": {tid: t.__dict__ for tid, t in self.active_traffic.items()},
            "clearances": {
                vid: {
                    "vehicle_id": c.vehicle_id,
                    "cleared_taxiways": list(c.cleared_taxiways),
                    "hold_short_runways": list(c.hold_short_runways),
                    "cleared_runway_crossings": list(c.cleared_runway_crossings),
                    "target_destination": c.target_destination,
                    "last_instruction_text": c.last_instruction_text
                }
                for vid, c in self.clearances.items()
            },
            "runway_safety_areas": [
                {
                    "id": primary_rwy.active_runway_id or "09L",
                    "x_min": primary_rwy.bounds[0],
                    "y_min": primary_rwy.bounds[1],
                    "x_max": primary_rwy.bounds[2],
                    "y_max": primary_rwy.bounds[3],
                    "status": "ACTIVE_HOT"
                }
            ]
        }
