"""
evaluator.py — Traffic Light Control Online Judge Platform (SUMO Engine)
=========================================================================
Securely executes an untrusted contestant script (solution.py),
steps through a 3600-tick simulation loop via stdin/stdout pipes,
enforces strict hardware limits, and calculates final metrics.

Uses REAL SUMO physics via TraCI. No dummy data.
"""

import argparse
import json
import subprocess
import sys
import time
import os
import tempfile
import traceback
import math
import shutil
from typing import List, Dict, Any

# ── Constants ─────────────────────────────────────────────────────────────────

TOTAL_TICKS = 3600
WALL_CLOCK_LIMIT = 1200.0  # 20 minutes for SUMO
RAM_LIMIT_MB = 256
RAM_LIMIT_BYTES = RAM_LIMIT_MB * 1024 * 1024

PHASE_NS_GREEN = 0
PHASE_EW_GREEN = 1
PHASE_YELLOW = 2

DEFAULT_NS_DURATION = 30
DEFAULT_EW_DURATION = 20
YELLOW_DURATION = 3

# ── Scenario Configs ─────────────────────────────────────────────────────────
# Maps difficulty levels 1-5 to traffic generation parameters.

SCENARIO_CONFIGS = {
    1: {"spawn_rate": 0.1, "bus_ratio": 0.05, "label": "Low Traffic"},
    2: {"spawn_rate": 0.25, "bus_ratio": 0.075, "label": "Light Traffic"},
    3: {"spawn_rate": 0.4, "bus_ratio": 0.1, "label": "Balanced"},
    4: {"spawn_rate": 0.6, "bus_ratio": 0.15, "label": "Heavy Traffic"},
    5: {"spawn_rate": 0.8, "bus_ratio": 0.2, "label": "Rush Hour"},
}

DEFAULT_LEVEL = 3

# ── SUMO XML Templates ───────────────────────────────────────────────────────
# Hardcoded 4-way intersection, 2 lanes per road. No netgenerate binary needed.

NET_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<net version="1.16" junctionCornerDetail="5" limitTurnSpeed="5.50"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/net_file.xsd">

    <location netOffset="500.00,500.00"
              convBoundary="-500.00,-500.00,500.00,500.00"
              origBoundary="-10000000000.00,-10000000000.00,10000000000.00,10000000000.00"
              projParameter="!"/>

    <!-- Internal edges for the intersection -->
    <edge id=":C_0" function="internal">
        <lane id=":C_0_0" index="0" speed="16.67" length="10.00" width="4.00" shape="2.00,-5.00 2.00,5.00"/>
    </edge>
    <edge id=":C_1" function="internal">
        <lane id=":C_1_0" index="0" speed="16.67" length="10.00" width="4.00" shape="5.00,0.00 -5.00,0.00"/>
    </edge>
    <edge id=":C_2" function="internal">
        <lane id=":C_2_0" index="0" speed="16.67" length="10.00" width="4.00" shape="-2.00,5.00 -2.00,-5.00"/>
    </edge>
    <edge id=":C_3" function="internal">
        <lane id=":C_3_0" index="0" speed="16.67" length="10.00" width="4.00" shape="-5.00,0.00 5.00,0.00"/>
    </edge>

    <!-- Approach edges (2 lanes each: right=lane 0, left=lane 1) -->
    <edge id="N_to_C" from="N" to="C" priority="2" numLanes="2" speed="16.67">
        <lane id="N_to_C_0" index="0" speed="16.67" length="500.00" width="4.00" shape="4.00,-500.00 4.00,0.00"/>
        <lane id="N_to_C_1" index="1" speed="16.67" length="500.00" width="4.00" shape="0.00,-500.00 0.00,0.00"/>
    </edge>
    <edge id="C_to_N" from="C" to="N" priority="2" numLanes="2" speed="16.67">
        <lane id="C_to_N_0" index="0" speed="16.67" length="500.00" width="4.00" shape="0.00,0.00 0.00,-500.00"/>
        <lane id="C_to_N_1" index="1" speed="16.67" length="500.00" width="4.00" shape="-4.00,0.00 -4.00,-500.00"/>
    </edge>
    <edge id="S_to_C" from="S" to="C" priority="2" numLanes="2" speed="16.67">
        <lane id="S_to_C_0" index="0" speed="16.67" length="500.00" width="4.00" shape="-4.00,500.00 -4.00,0.00"/>
        <lane id="S_to_C_1" index="1" speed="16.67" length="500.00" width="4.00" shape="0.00,500.00 0.00,0.00"/>
    </edge>
    <edge id="C_to_S" from="C" to="S" priority="2" numLanes="2" speed="16.67">
        <lane id="C_to_S_0" index="0" speed="16.67" length="500.00" width="4.00" shape="0.00,0.00 0.00,500.00"/>
        <lane id="C_to_S_1" index="1" speed="16.67" length="500.00" width="4.00" shape="4.00,0.00 4.00,500.00"/>
    </edge>
    <edge id="E_to_C" from="E" to="C" priority="2" numLanes="2" speed="16.67">
        <lane id="E_to_C_0" index="0" speed="16.67" length="500.00" width="4.00" shape="500.00,-4.00 0.00,-4.00"/>
        <lane id="E_to_C_1" index="1" speed="16.67" length="500.00" width="4.00" shape="500.00,0.00 0.00,0.00"/>
    </edge>
    <edge id="C_to_E" from="C" to="E" priority="2" numLanes="2" speed="16.67">
        <lane id="C_to_E_0" index="0" speed="16.67" length="500.00" width="4.00" shape="0.00,0.00 500.00,0.00"/>
        <lane id="C_to_E_1" index="1" speed="16.67" length="500.00" width="4.00" shape="0.00,4.00 500.00,4.00"/>
    </edge>
    <edge id="W_to_C" from="W" to="C" priority="2" numLanes="2" speed="16.67">
        <lane id="W_to_C_0" index="0" speed="16.67" length="500.00" width="4.00" shape="-500.00,4.00 0.00,4.00"/>
        <lane id="W_to_C_1" index="1" speed="16.67" length="500.00" width="4.00" shape="-500.00,0.00 0.00,0.00"/>
    </edge>
    <edge id="C_to_W" from="C" to="W" priority="2" numLanes="2" speed="16.67">
        <lane id="C_to_W_0" index="0" speed="16.67" length="500.00" width="4.00" shape="0.00,0.00 -500.00,0.00"/>
        <lane id="C_to_W_1" index="1" speed="16.67" length="500.00" width="4.00" shape="0.00,-4.00 -500.00,-4.00"/>
    </edge>

    <!-- Junctions -->
    <junction id="C" type="traffic_light" x="0.00" y="0.00"
              incLanes="N_to_C_0 N_to_C_1 E_to_C_0 E_to_C_1 S_to_C_0 S_to_C_1 W_to_C_0 W_to_C_1"
              intLanes=":C_0_0 :C_1_0 :C_2_0 :C_3_0"
              shape="-5.00,0.00 5.00,0.00 5.00,0.00 5.00,0.00 5.00,0.00 5.00,0.00 5.00,0.00 -5.00,0.00 -5.00,0.00 -5.00,0.00 -5.00,0.00 -5.00,0.00 -5.00,0.00">
        <request index="0" response="0000" foes="0000" cont="0"/>
        <request index="1" response="0000" foes="0000" cont="0"/>
        <request index="2" response="0000" foes="0000" cont="0"/>
        <request index="3" response="0000" foes="0000" cont="0"/>
    </junction>
    <junction id="N" type="dead_end" x="0.00" y="-500.00" incLanes="C_to_N_0 C_to_N_1" intLanes="" shape="0.00,-500.00 0.00,-500.00"/>
    <junction id="S" type="dead_end" x="0.00" y="500.00" incLanes="C_to_S_0 C_to_S_1" intLanes="" shape="0.00,500.00 0.00,500.00"/>
    <junction id="E" type="dead_end" x="500.00" y="0.00" incLanes="C_to_E_0 C_to_E_1" intLanes="" shape="500.00,0.00 500.00,0.00"/>
    <junction id="W" type="dead_end" x="-500.00" y="0.00" incLanes="C_to_W_0 C_to_W_1" intLanes="" shape="-500.00,0.00 -500.00,0.00"/>

    <!-- Connections -->
    <connection from="N_to_C" to="C_to_S" fromLane="0" toLane="0" tl="C" linkIndex="0" dir="s" state="o"/>
    <connection from="E_to_C" to="C_to_W" fromLane="0" toLane="0" tl="C" linkIndex="1" dir="s" state="o"/>
    <connection from="S_to_C" to="C_to_N" fromLane="0" toLane="0" tl="C" linkIndex="2" dir="s" state="o"/>
    <connection from="W_to_C" to="C_to_E" fromLane="0" toLane="0" tl="C" linkIndex="3" dir="s" state="o"/>

    <connection from=":C_0" to="C_to_S" fromLane="0" toLane="0" dir="s" state="M"/>
    <connection from=":C_1" to="C_to_W" fromLane="0" toLane="0" dir="s" state="M"/>
    <connection from=":C_2" to="C_to_N" fromLane="0" toLane="0" dir="s" state="M"/>
    <connection from=":C_3" to="C_to_E" fromLane="0" toLane="0" dir="s" state="M"/>

    <!-- TLS Logic: GGgg = NS green, ggGG = EW green, yyyy = yellow -->
    <tlLogic id="C" type="static" programID="0" offset="0">
        <phase duration="30" state="GGgg"/>
        <phase duration="3"  state="yyyy"/>
        <phase duration="20" state="ggGG"/>
        <phase duration="3"  state="yyyy"/>
    </tlLogic>
</net>"""


def generate_rou_xml(level_config: Dict[str, Any]) -> str:
    """
    Generate routes XML dynamically based on scenario level parameters.
    spawn_rate controls vehicle density (vehsPerHour = spawn_rate * 5000).
    bus_ratio controls the proportion of bus vTypes (slower, longer vehicles).
    """
    spawn_rate = level_config["spawn_rate"]
    bus_ratio = level_config["bus_ratio"]

    vehs_per_hour = int(spawn_rate * 5000)
    bus_vehs_per_hour = int(vehs_per_hour * bus_ratio)
    car_vehs_per_hour = vehs_per_hour - bus_vehs_per_hour

    routes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '        xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/routes_file.xsd">',
        "",
        '    <vType id="car" length="5.0" width="2.0" maxSpeed="16.67"',
        '           accel="2.0" decel="4.5" minGap="2.5" color="0,150,200"/>',
        '    <vType id="bus" length="12.0" width="2.5" maxSpeed="11.11"',
        '           accel="1.2" decel="3.0" minGap="5.0" color="200,100,0"/>',
        "",
    ]

    directions = [
        ("NS", "N_to_C", "C_to_S"),
        ("SN", "S_to_C", "C_to_N"),
        ("EW", "E_to_C", "C_to_W"),
        ("WE", "W_to_C", "C_to_E"),
    ]

    for dir_id, from_edge, to_edge in directions:
        for lane_idx in range(2):
            route_id = f"{dir_id}_{lane_idx}"
            if car_vehs_per_hour > 0:
                routes.append(
                    f'    <flow id="car_{route_id}" type="car" from="{from_edge}" to="{to_edge}"'
                    f' begin="0" end="3600" vehsPerHour="{car_vehs_per_hour}" departLane="{lane_idx}"/>'
                )
            if bus_vehs_per_hour > 0:
                routes.append(
                    f'    <flow id="bus_{route_id}" type="bus" from="{from_edge}" to="{to_edge}"'
                    f' begin="0" end="3600" vehsPerHour="{bus_vehs_per_hour}" departLane="{lane_idx}"/>'
                )

    routes.append("</routes>")
    return "\n".join(routes)


ROU_XML_TEMPLATE = generate_rou_xml(SCENARIO_CONFIGS[DEFAULT_LEVEL])

SUMOCFG_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/sumoConfiguration.xsd">
    <input>
        <net-file value="net.net.xml"/>
        <route-files value="routes.rou.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="3600"/>
        <step-length value="1.0"/>
    </time>
    <report>
        <no-warnings value="true"/>
        <no-step-log value="true"/>
    </report>
    <random_number>
        <random value="true"/>
        <seed value="42"/>
    </random_number>
</configuration>"""


# ── SUMO Simulation Engine ───────────────────────────────────────────────────


class SumoSimulationEngine:
    """
    Real SUMO physics engine via TraCI.
    Manages a 4-way intersection with 2 lanes per road.
    """

    # Edge-to-direction mapping
    EDGE_TO_DIR = {
        "N_to_C": "N",
        "S_to_C": "S",
        "E_to_C": "E",
        "W_to_C": "W",
    }

    # Sensor is 150m from intersection → vehicles at distance 150 from center
    SENSOR_THRESHOLD = 150.0

    def __init__(self, tmpdir: str, level_config: Dict[str, Any] = None):  # type: ignore
        import traci
        import sumolib

        self.tmpdir = tmpdir
        self.traci = traci
        self.level_config = level_config or SCENARIO_CONFIGS[DEFAULT_LEVEL]

        # Write SUMO files
        self._write_files()

        # Find SUMO binary
        import shutil

        sumo_binary = shutil.which("sumo") or shutil.which("sumo-gui")
        if not sumo_binary:
            raise RuntimeError(
                "SUMO binary not found in PATH. Install sumo or set PATH."
            )

        cfg_path = os.path.join(tmpdir, "sumo.sumocfg")

        # Start SUMO headless
        traci.start(
            [
                sumo_binary,
                "-c",
                cfg_path,
                "--no-step-log",
                "true",
                "--no-warnings",
                "true",
                "--quit-on-end",
            ]
        )

    def _write_files(self):
        """Write node/edge XML, run netconvert, write rou.xml, det.xml, sumocfg."""
        nod_xml = """<?xml version="1.0" encoding="UTF-8"?>
<nodes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/nodes_file.xsd">
    <node id="C" x="0.0" y="0.0" type="traffic_light"/>
    <node id="N" x="0.0" y="-500.0" type="priority"/>
    <node id="S" x="0.0" y="500.0" type="priority"/>
    <node id="E" x="500.0" y="0.0" type="priority"/>
    <node id="W" x="-500.0" y="0.0" type="priority"/>
</nodes>"""
        edg_xml = """<?xml version="1.0" encoding="UTF-8"?>
<edges xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/edges_file.xsd">
    <edge id="N_to_C" from="N" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_N" from="C" to="N" priority="2" numLanes="2" speed="16.67"/>
    <edge id="S_to_C" from="S" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_S" from="C" to="S" priority="2" numLanes="2" speed="16.67"/>
    <edge id="E_to_C" from="E" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_E" from="C" to="E" priority="2" numLanes="2" speed="16.67"/>
    <edge id="W_to_C" from="W" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_W" from="C" to="W" priority="2" numLanes="2" speed="16.67"/>
</edges>"""
        det_xml = """<?xml version="1.0" encoding="UTF-8"?>
<additional xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/additional_file.xsd">
    <inductionLoop id="sensor_N" lane="N_to_C_0" pos="100" freq="1" file="NUL"/>
    <inductionLoop id="sensor_S" lane="S_to_C_0" pos="100" freq="1" file="NUL"/>
    <inductionLoop id="sensor_E" lane="E_to_C_0" pos="100" freq="1" file="NUL"/>
    <inductionLoop id="sensor_W" lane="W_to_C_0" pos="100" freq="1" file="NUL"/>
</additional>"""

        nod_path = os.path.join(self.tmpdir, "nodes.nod.xml")
        edg_path = os.path.join(self.tmpdir, "edges.edg.xml")
        net_path = os.path.join(self.tmpdir, "net.net.xml")

        with open(nod_path, "w") as f:
            f.write(nod_xml)
        with open(edg_path, "w") as f:
            f.write(edg_xml)

        netconvert = shutil.which("netconvert")
        if not netconvert:
            raise RuntimeError("netconvert binary not found in PATH")
        result = subprocess.run(
            [
                netconvert,
                "--node-files=" + nod_path,
                "--edge-files=" + edg_path,
                "--output-file=" + net_path,
                "--no-internal-links",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"netconvert failed: {result.stderr}")

        with open(os.path.join(self.tmpdir, "routes.rou.xml"), "w") as f:
            f.write(generate_rou_xml(self.level_config))
        with open(os.path.join(self.tmpdir, "det.xml"), "w") as f:
            f.write(det_xml)

        sumocfg = """<?xml version="1.0" encoding="UTF-8"?>
<configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/sumoConfiguration.xsd">
    <input>
        <net-file value="net.net.xml"/>
        <route-files value="routes.rou.xml"/>
        <additional-files value="det.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="3600"/>
        <step-length value="1.0"/>
    </time>
    <report>
        <no-warnings value="true"/>
        <no-step-log value="true"/>
    </report>
    <random_number>
        <random value="true"/>
        <seed value="42"/>
    </random_number>
</configuration>"""
        with open(os.path.join(self.tmpdir, "sumo.sumocfg"), "w") as f:
            f.write(sumocfg)

    def set_tls_state(self, ns_green: bool, ew_green: bool):
        """
        Set traffic light state via TraCI.
        Dynamically discovers link count and builds state string.
        """
        try:
            # Get the current default state to know link count
            default_state = self.traci.trafficlight.getRedYellowGreenState("C")
            link_count = len(default_state)
        except self.traci.TraCIException:
            return

        # Determine which links correspond to NS vs EW
        # Netconvert orders links by incoming edge, then by connection index
        # For a 4-way intersection: links 0..half = NS approaches, half..end = EW approaches
        half = link_count // 2

        if ns_green:
            state = "G" * half + "r" * (link_count - half)
        elif ew_green:
            state = "r" * half + "G" * (link_count - half)
        else:
            state = "y" * link_count

        try:
            self.traci.trafficlight.setRedYellowGreenState("C", state)
        except self.traci.TraCIException:
            pass

    def step(self) -> List[Dict]:
        """
        Advance simulation by 1 tick.
        Uses E1 induction loops for sensor events — NO per-vehicle loops.
        """
        # Advance SUMO by 1 second FIRST, then read detector data
        self.traci.simulationStep()

        # Read E1 induction loop data (only 4 calls, not N vehicle calls)
        sensor_events: List[Dict] = []
        detector_map = {
            "sensor_N": "N",
            "sensor_S": "S",
            "sensor_E": "E",
            "sensor_W": "W",
        }
        for det_id, direction in detector_map.items():
            try:
                vids = self.traci.inductionloop.getLastStepVehicleIDs(det_id)
                for vid in vids:
                    # Only 2 TraCI calls per detected vehicle (not per ALL vehicles)
                    speed = self.traci.vehicle.getSpeed(vid)
                    length = self.traci.vehicle.getLength(vid)
                    lane = self.traci.vehicle.getLaneID(vid)
                    sensor_events.append(
                        {
                            "id": vid,
                            "dir": direction,
                            "lane": lane.split("_")[-1] if "_" in lane else "0",
                            "speed": round(speed, 1),
                            "length": round(length, 1),
                        }
                    )
            except self.traci.TraCIException:
                pass

        return sensor_events

    def get_light_states(self) -> Dict[str, Any]:
        """Get current TLS state and map to human-readable colors."""
        try:
            state = self.traci.trafficlight.getRedYellowGreenState("C")
        except self.traci.TraCIException:
            state = "GGgg"

        if state.startswith("G"):
            lights = {"N": "GREEN", "S": "GREEN", "E": "RED", "W": "RED"}
        elif state.startswith("g"):
            lights = {"N": "RED", "S": "RED", "E": "GREEN", "W": "GREEN"}
        else:
            lights = {"N": "YELLOW", "S": "YELLOW", "E": "YELLOW", "W": "YELLOW"}

        lights["countdown"] = 0
        return lights

    def get_metrics(self) -> Dict[str, Any]:
        """Calculate metrics using edge-level TraCI calls — NO per-vehicle loops."""
        # Total delay: sum waiting time per edge (4 calls)
        total_delay = 0.0
        for edge_id in ["N_to_C", "S_to_C", "E_to_C", "W_to_C"]:
            try:
                total_delay += self.traci.edge.getWaitingTime(edge_id)
            except self.traci.TraCIException:
                pass

        # Max queue: sum halting vehicles per edge (4 calls)
        max_queue = 0
        for edge_id in ["N_to_C", "S_to_C", "E_to_C", "W_to_C"]:
            try:
                max_queue += self.traci.edge.getLastStepHaltingNumber(edge_id)
            except self.traci.TraCIException:
                pass

        # Throughput: arrived vehicles this step
        throughput = self.traci.simulation.getArrivedNumber()

        return {
            "total_delay": round(total_delay, 2),
            "max_queue_length": max_queue,
            "throughput": throughput,
        }

    def get_replay_frame(self, tick: int, phase: str, in_yellow: bool, queues: Dict[str, int]) -> Dict[str, Any]:
        """
        Capture a single replay frame with vehicles and light states.
        x, y are raw world meters: (0,0) = intersection center, +Y = North, +X = East.
        """
        # Get light states as single-char codes
        try:
            state = self.traci.trafficlight.getRedYellowGreenState("C")
        except self.traci.TraCIException:
            state = "GGgg"

        if state.startswith("G"):
            lights = {"N": "G", "S": "G", "E": "r", "W": "r"}
        elif state.startswith("g"):
            lights = {"N": "r", "S": "r", "E": "G", "W": "G"}
        else:
            lights = {"N": "y", "S": "y", "E": "y", "W": "y"}

        # Get all vehicles with positions
        vehicles: List[Dict] = []
        for vid in self.traci.vehicle.getIDList():
            try:
                x, y = self.traci.vehicle.getPosition(vid)
                angle = self.traci.vehicle.getAngle(vid)
                # Convert SUMO angle (0=North, clockwise) to math radians (0=East, CCW)
                angle_rad = math.pi / 2 - math.radians(angle)
                # Get vehicle color from type
                color = self.traci.vehicle.getColor(vid)
                color_str = (
                    f"rgb({color[0]},{color[1]},{color[2]})" if color else "blue"
                )
                vehicles.append(
                    {
                        "id": vid,
                        "x": round(x, 4),
                        "y": round(y, 4),
                        "angle": round(angle_rad, 4),
                        "color": color_str,
                    }
                )
            except self.traci.TraCIException:
                continue

        return {
            "tick": tick,
            "lights": lights,
            "vehicles": vehicles,
            "phase": phase,
            "in_yellow": in_yellow,
            "queues": queues
        }

    def close(self):
        """Cleanly close TraCI connection."""
        try:
            self.traci.close()
        except Exception:
            pass


# ── Sandbox Launcher ─────────────────────────────────────────────────────────


def _set_resource_limits():
    """Pre-exec function: hard-limit RAM to 256MB, disable core dumps."""
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (RAM_LIMIT_BYTES, RAM_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def launch_sandbox(solution_path: str) -> subprocess.Popen:
    """
    Launch contestant's solution.py as a sandboxed subprocess.
    NEVER uses shell=True.
    """
    if not os.path.isfile(solution_path):
        raise FileNotFoundError(f"Solution not found: {solution_path}")

    cmd = [sys.executable, "-u", solution_path]
    preexec = _set_resource_limits if sys.platform != "win32" else None

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(solution_path)),
            preexec_fn=preexec,
        )
        return proc
    except Exception as e:
        raise RuntimeError(f"Failed to launch sandbox: {e}")


# ── Evaluation Loop ──────────────────────────────────────────────────────────


def evaluate(solution_path: str, level: int = DEFAULT_LEVEL) -> Dict[str, Any]:
    """
    Main evaluation function with REAL SUMO physics.
    """
    import math  # needed by SumoSimulationEngine

    if level not in SCENARIO_CONFIGS:
        raise ValueError(
            f"Invalid level {level}. Must be one of {sorted(SCENARIO_CONFIGS.keys())}"
        )

    level_config = SCENARIO_CONFIGS[level]

    start_time = time.time()
    engine = None
    proc = None

    try:
        # Create temp directory for SUMO files
        tmpdir = tempfile.mkdtemp()

        # ── Initialize SUMO Engine ──────────────────────────────────────
        try:
            engine = SumoSimulationEngine(tmpdir, level_config)
        except Exception as e:
            return {
                "status": "ENGINE_ERROR",
                "error_log": f"SUMO initialization failed: {str(e)}\n{traceback.format_exc()}",
                "score": 0,
            }

        # ── Launch Contestant Sandbox ───────────────────────────────────
        try:
            proc = launch_sandbox(solution_path)
        except (FileNotFoundError, RuntimeError) as e:
            return {"status": "START_FAILED", "error": str(e), "score": 0}

        # ── Phase tracking ──────────────────────────────────────────────
        current_phase = PHASE_NS_GREEN
        last_green_phase = PHASE_NS_GREEN
        phase_timer = 0
        phase_duration = DEFAULT_NS_DURATION
        cumulative_delay = 0.0
        peak_queue = 0
        total_throughput = 0
        replay_data: List[Dict[str, Any]] = []

        # ── 3600-Tick Loop ──────────────────────────────────────────────
        for tick in range(1, TOTAL_TICKS + 1):
            # TLE check
            elapsed = time.time() - start_time
            if elapsed > WALL_CLOCK_LIMIT:
                proc.kill()
                return {
                    "status": "TLE",
                    "error": f"Time Limit Exceeded ({WALL_CLOCK_LIMIT}s)",
                    "ticks_completed": tick - 1,
                    "score": 0,
                    "replay_data": replay_data,
                }

            # Set TLS based on current phase
            ns_green = current_phase == PHASE_NS_GREEN
            ew_green = current_phase == PHASE_EW_GREEN
            engine.set_tls_state(ns_green, ew_green)

            # Step SUMO
            try:
                sensor_events = engine.step()
            except Exception as e:
                proc.kill()
                return {
                    "status": "ENGINE_ERROR",
                    "error_log": f"SUMO step failed at tick {tick}: {str(e)}\n{traceback.format_exc()}",
                    "ticks_completed": tick - 1,
                    "score": 0,
                    "replay_data": replay_data,
                }

            # Gather Queues (Halting Number)
            queues = {"N": 0, "S": 0, "E": 0, "W": 0}
            for edge, d in zip(["N_to_C", "S_to_C", "E_to_C", "W_to_C"], ["N", "S", "E", "W"]):
                try:
                    queues[d] = engine.traci.edge.getLastStepHaltingNumber(edge)
                except Exception:
                    pass
            
            # Map state for UI and Contestant
            if current_phase == PHASE_NS_GREEN:
                phase_str, in_yellow = 'NS', False
            elif current_phase == PHASE_EW_GREEN:
                phase_str, in_yellow = 'EW', False
            else:
                phase_str, in_yellow = ('NS' if last_green_phase == PHASE_NS_GREEN else 'EW'), True

            # Capture replay frame (vehicles + lights at raw world coords)
            replay_data.append(engine.get_replay_frame(tick, phase_str, in_yellow, queues))

            # Send to contestant
            light_states = engine.get_light_states()
            payload = {
                "tick": tick,
                "lights": light_states,
                "sensor_events": sensor_events,
                "queues": queues,
                "phase": phase_str,
                "phase_timer": phase_timer,
            }

            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, IOError, OSError):
                proc.kill()
                return {
                    "status": "RTE",
                    "error": f"Contestant crashed at tick {tick} (broken pipe)",
                    "ticks_completed": tick - 1,
                    "score": 0,
                }

            # Read contestant response
            try:
                response_line = proc.stdout.readline()
                if not response_line:
                    proc.kill()
                    return {
                        "status": "RTE",
                        "error": f"Contestant exited prematurely at tick {tick}",
                        "ticks_completed": tick - 1,
                        "score": 0,
                    }
                action = json.loads(response_line.strip())
            except json.JSONDecodeError:
                proc.kill()
                return {
                    "status": "RTE",
                    "error": f"Invalid JSON from contestant at tick {tick}",
                    "ticks_completed": tick - 1,
                    "score": 0,
                }

            # Process contestant action
            cmd = action.get("action", "GIU_NGUYEN")
            if cmd == "CHUYEN_PHA":
                new_duration = action.get("duration")
                if new_duration is not None and isinstance(new_duration, (int, float)):
                    phase_duration = max(1, int(new_duration))

            # Advance phase
            phase_timer += 1
            if phase_timer >= phase_duration:
                if current_phase == PHASE_YELLOW:
                    # Switch to opposite green
                    current_phase = (
                        PHASE_EW_GREEN
                        if last_green_phase == PHASE_NS_GREEN
                        else PHASE_NS_GREEN
                    )
                    last_green_phase = current_phase
                    phase_duration = DEFAULT_EW_DURATION if current_phase == PHASE_EW_GREEN else DEFAULT_NS_DURATION
                else:
                    # We are on a green phase, enter yellow
                    last_green_phase = current_phase
                    current_phase = PHASE_YELLOW
                    phase_duration = YELLOW_DURATION
                phase_timer = 0

            # Accumulate metrics
            metrics = engine.get_metrics()
            cumulative_delay += metrics["total_delay"]
            peak_queue = max(peak_queue, metrics["max_queue_length"])
            total_throughput += metrics["throughput"]

    except Exception as e:
        return {
            "status": "INTERNAL_ERROR",
            "error_log": f"Unexpected error: {str(e)}\n{traceback.format_exc()}",
            "score": 0,
        }

    finally:
        # Cleanup
        if engine:
            engine.close()
        if proc:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    # ── Final Scoring ───────────────────────────────────────────────────
    avg_delay = cumulative_delay / TOTAL_TICKS if TOTAL_TICKS > 0 else 0
    score = max(0.0, min(100.0, 100.0 - (avg_delay / 10.0)))

    return {
        "level": level,
        "level_label": level_config["label"],
        "spawn_rate": level_config["spawn_rate"],
        "bus_ratio": level_config["bus_ratio"],
        "status": "OK",
        "ticks_completed": TOTAL_TICKS,
        "total_delay": round(cumulative_delay, 2),
        "max_queue_length": peak_queue,
        "throughput": total_throughput,
        "score": round(score, 2),
        "replay_data": replay_data,
    }


# ── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Traffic Light Control Online Judge — SUMO Evaluator"
    )
    parser.add_argument(
        "solution",
        nargs="?",
        default="solution.py",
        help="Path to contestant's solution script (default: solution.py)",
    )
    parser.add_argument(
        "--level",
        type=int,
        default=DEFAULT_LEVEL,
        choices=sorted(SCENARIO_CONFIGS.keys()),
        help=f"Difficulty level 1-5 (default: {DEFAULT_LEVEL}). "
        f"1={SCENARIO_CONFIGS[1]['label']}, "
        f"3={SCENARIO_CONFIGS[3]['label']}, "
        f"5={SCENARIO_CONFIGS[5]['label']}",
    )
    args = parser.parse_args()

    solution_path = args.solution
    level = args.level
    level_config = SCENARIO_CONFIGS[level]

    print(f"[*] Evaluating: {solution_path}")
    print(f"[*] Engine: SUMO (TraCI)")
    print(f"[*] Level: {level} ({level_config['label']})")
    print(f"[*] Spawn rate: {level_config['spawn_rate']}")
    print(f"[*] Bus ratio: {level_config['bus_ratio']}")
    print(f"[*] Ticks: {TOTAL_TICKS}")
    print(f"[*] Wall-clock limit: {WALL_CLOCK_LIMIT}s")
    print(f"[*] RAM limit: {RAM_LIMIT_MB}MB")
    print("=" * 60)

    result = evaluate(solution_path, level=level)

    print("=" * 60)
    print(json.dumps(result, indent=2))
