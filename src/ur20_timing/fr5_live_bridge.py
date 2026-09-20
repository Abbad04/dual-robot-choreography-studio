"""Loopback-only, fail-closed FAIRINO FR5 browser companion.

The browser never talks to a robot controller directly.  This process serves
the committed choreography frontend and is the only component allowed to use
FAIRINO's official Python SDK.  Connecting is read-only by default; physical
motion requires ``--allow-motion`` at process startup and an explicit operator
confirmation in the browser for every live-control or playback session.

No physical robot is contacted merely by importing this module or starting the
HTTP server.  A connection is opened only after an authenticated ``/connect``
request is made.  The bundled local page uses a launch cookie; a deployed
HTTPS page can pair to this loopback process with the launch code printed in
the connector window.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from collections import deque
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit
import uuid


PROFILE_FINGERPRINT = (
    "d8319199f34d0d9aa4856471242a481a9e71c0a88379caaade3d5ca3c7f0fadf"
)
SERVO_PERIOD_S = 0.008
MAX_LUA_BYTES = 16 * 1024 * 1024
MAX_TRAJECTORY_FRAMES = 250_000
TRAJECTORY_ENCODING = "fr5-delta-microdegree-u32-us-v1"
TRAJECTORY_RECORD_BYTES = 28
TRAJECTORY_SCALE_DEG = 1e-6
DEFAULT_ROBOT_IP = "192.168.58.2"
DEFAULT_JOG_SPEED_DEG_S = 5.0
DEFAULT_PLAYBACK_SPEED_PERCENT = 10
SERVO_WATCHDOG_S = 0.35
TELEMETRY_FRESH_S = 0.75
START_TOLERANCE_RAD = math.radians(0.5)
STATIONARY_TOLERANCE_RAD_S = math.radians(1.0)
JOINT_DOMAIN_RAD = (
    (math.radians(-174), math.radians(174)),
    (math.radians(-264), math.radians(84)),
    (math.radians(-160.998), math.radians(160.998)),
    (math.radians(-264), math.radians(84)),
    (math.radians(-174), math.radians(174)),
    (math.radians(-179), math.radians(179)),
)
JOINT_MAX_SPEED_RAD_S = tuple(
    math.radians(value) for value in (120, 120, 180, 180, 180, 180)
)
JOINT_MAX_ACCELERATION_RAD_S2 = tuple(math.radians(360) for _ in range(6))
TERMINAL_RUN_STATES = frozenset({"completed", "aborted", "failed"})
_PROGRAM_NAME = re.compile(r"RAW_[A-Za-z0-9_]{1,80}\.lua\Z", re.ASCII)
_CONNECTOR_LANDING_HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><title>FR5 Connector</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<body style="font:16px system-ui;max-width:42rem;margin:4rem auto;padding:0 1rem;line-height:1.5">
<h1>FR5 Connector is running</h1>
<p>Use the pairing code shown in the connector window on the deployed
choreography web app. Keep the connector window open while operating the robot.</p>
</body></html>
"""


class Fr5BridgeError(RuntimeError):
    """A connection or physical-control precondition failed."""


class Fr5BridgeConflict(Fr5BridgeError):
    """A second motion request conflicted with an active one."""


class _HTTPInputError(Fr5BridgeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    """Make a path absolute without asking macOS to resolve protected parents."""

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return Path(os.path.abspath(os.fspath(candidate)))


def _error_code(result: object, operation: str) -> int:
    value = result[0] if isinstance(result, (tuple, list)) else result
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Fr5BridgeError(f"{operation} returned an invalid result")
    code = int(value)
    if code != 0:
        detail = ""
        if isinstance(result, (tuple, list)) and len(result) > 1:
            candidate = result[-1]
            if isinstance(candidate, str) and candidate.strip():
                detail = f": {candidate.strip()}"
        raise Fr5BridgeError(f"{operation} failed with FAIRINO code {code}{detail}")
    return code


def _result_value(result: object, operation: str) -> object:
    if not isinstance(result, (tuple, list)) or len(result) < 2:
        raise Fr5BridgeError(f"{operation} returned no value")
    _error_code(result, operation)
    return result[1]


def _finite_vector(
    value: object, *, field: str, length: int = 6
) -> tuple[float, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise Fr5BridgeError(f"{field} must contain {length} numbers")
    try:
        items = tuple(value)  # type: ignore[arg-type]
        if any(isinstance(item, bool) for item in items):
            raise TypeError
        result = tuple(float(item) for item in items)
    except (TypeError, ValueError, OverflowError) as exc:
        raise Fr5BridgeError(f"{field} must contain {length} numbers") from exc
    if len(result) != length or any(not math.isfinite(item) for item in result):
        raise Fr5BridgeError(f"{field} must contain {length} finite numbers")
    return result


def _private_ipv4(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise Fr5BridgeError("robot IP must be a private IPv4 address") from exc
    private_networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    if (
        address.version != 4
        or not any(address in network for network in private_networks)
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    ):
        raise Fr5BridgeError("robot IP must be a private, non-loopback IPv4 address")
    return str(address)


def _secure_browser_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise Fr5BridgeError("invalid browser Origin") from exc
    host_name = (parsed.hostname or "").lower()
    secure_scheme = parsed.scheme == "https"
    loopback_development = parsed.scheme == "http" and host_name in {
        "127.0.0.1",
        "localhost",
    }
    if (
        not (secure_scheme or loopback_development)
        or not host_name
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise Fr5BridgeError("FR5 pairing requires HTTPS or a loopback development page")
    host = host_name
    default_port = 443 if secure_scheme else 80
    if port is not None and port != default_port:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def _attribute(package: object, *names: str) -> object | None:
    for name in names:
        if hasattr(package, name):
            return getattr(package, name)
    return None


class RobotAdapter(Protocol):
    def connect(self, robot_ip: str) -> None: ...

    def close(self) -> None: ...

    def read_state(self) -> dict[str, object]: ...

    def servo_start(self) -> None: ...

    def servo_target(
        self, q_deg: Sequence[float], *, period_s: float, command_id: int
    ) -> None: ...

    def servo_end(self) -> None: ...

    def stop_motion(self) -> None: ...

    def upload_program(self, local_path: Path, remote_path: str) -> None: ...

    def set_speed(self, percent: int) -> None: ...

    def start_joint_move(self, q_deg: Sequence[float], *, speed_percent: int) -> None: ...

    def run_program(self) -> None: ...

    def pause_motion(self) -> None: ...

    def resume_motion(self) -> None: ...

    def pause_program(self) -> None: ...

    def resume_program(self) -> None: ...

    def stop_program(self) -> None: ...

    def program_state(self) -> int: ...

    def current_program_line(self) -> int: ...


class FairinoSdkAdapter:
    """Narrow wrapper around the official ``fairino.Robot`` API."""

    def __init__(self, robot_factory: Any | None = None) -> None:
        self._robot_factory = robot_factory
        self._robot: Any | None = None

    @property
    def robot(self) -> Any:
        if self._robot is None:
            raise Fr5BridgeError("FR5 is not connected")
        return self._robot

    def connect(self, robot_ip: str) -> None:
        if self._robot is not None:
            return
        factory = self._robot_factory
        if factory is None:
            if sys.platform == "darwin":
                try:
                    from .fr5_macos_sdk import load_official_robot_module

                    Robot = load_official_robot_module(robot_ip)
                except Exception as exc:
                    raise Fr5BridgeError(str(exc)) from exc
            else:
                try:
                    from fairino import Robot  # type: ignore[import-not-found]
                except ImportError as exc:
                    raise Fr5BridgeError(
                        "the FAIRINO Python SDK is not installed; install the SDK version matching the robot controller"
                    ) from exc
            factory = Robot.RPC
        try:
            robot = factory(robot_ip)
        except Exception as exc:
            raise Fr5BridgeError(
                f"FAIRINO SDK could not connect to {robot_ip}"
            ) from exc
        if robot is None:
            raise Fr5BridgeError(f"FAIRINO SDK could not connect to {robot_ip}")
        self._robot = robot
        try:
            communication = int(
                _result_value(robot.GetSDKComState(), "GetSDKComState")
            )
            if communication != 0:
                raise Fr5BridgeError("FAIRINO SDK reports abnormal communication")
            watchdog = getattr(robot, "SetRobotStopOnComDisc", None)
            if watchdog is None:
                raise Fr5BridgeError(
                    "the installed FAIRINO SDK lacks the communication-loss stop safeguard"
                )
            _error_code(
                watchdog(0, True, 250),
                "SetRobotStopOnComDisc",
            )
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        robot, self._robot = self._robot, None
        if robot is not None:
            try:
                robot.CloseRPC()
            except Exception:
                pass

    def read_state(self) -> dict[str, object]:
        robot = self.robot
        q_deg = _finite_vector(
            _result_value(
                robot.GetActualJointPosDegree(1), "GetActualJointPosDegree"
            ),
            field="measured FR5 joint position",
        )
        qd_deg_s = _finite_vector(
            _result_value(
                robot.GetActualJointSpeedsDegree(1),
                "GetActualJointSpeedsDegree",
            ),
            field="measured FR5 joint speed",
        )
        tcp_pose = _finite_vector(
            _result_value(robot.GetActualTCPPose(1), "GetActualTCPPose"),
            field="measured FR5 tool pose",
        )
        emergency_stop = bool(
            int(
                _result_value(
                    robot.GetRobotEmergencyStopState(),
                    "GetRobotEmergencyStopState",
                )
            )
        )
        communication_state = int(
            _result_value(robot.GetSDKComState(), "GetSDKComState")
        )
        safety_result = robot.GetSafetyStopState()
        _error_code(safety_result, "GetSafetyStopState")
        safety_value: object
        if isinstance(safety_result, (tuple, list)) and len(safety_result) >= 3:
            safety_value = safety_result[1:3]
        else:
            safety_value = _result_value(safety_result, "GetSafetyStopState")
        safety = _finite_vector(
            safety_value, field="FR5 safety stop state", length=2
        )
        program_state = self.program_state()

        package: object = getattr(robot, "robot_state_pkg", object())
        get_realtime = getattr(robot, "GetRobotRealTimeState", None)
        if get_realtime is not None:
            try:
                package = _result_value(
                    get_realtime(), "GetRobotRealTimeState"
                )
            except Fr5BridgeError:
                # The required point/safety queries above still remain
                # authoritative on older matching SDK releases.
                package = getattr(robot, "robot_state_pkg", package)
        enabled_raw = _attribute(
            package, "rbt_enable_state", "rbtEnableState", "servo_enable"
        )
        main_code_raw = _attribute(package, "main_code", "mainCode")
        sub_code_raw = _attribute(package, "sub_code", "subCode")
        robot_mode_raw = _attribute(package, "robot_mode", "robotMode")
        robot_state_raw = _attribute(package, "robot_state", "robotState")
        enabled = None if enabled_raw is None else bool(int(enabled_raw))
        main_code = None if main_code_raw is None else int(main_code_raw)
        sub_code = None if sub_code_raw is None else int(sub_code_raw)
        faulted = (
            main_code is None
            or sub_code is None
            or main_code != 0
            or sub_code != 0
        )
        safety_ok = bool(
            communication_state == 0
            and not emergency_stop
            and not any(bool(int(item)) for item in safety)
            and enabled is True
            and not faulted
        )
        return {
            "actual_q_rad": [math.radians(value) for value in q_deg],
            "actual_qd_rad_s": [math.radians(value) for value in qd_deg_s],
            "tcp_pose_mm_deg": list(tcp_pose),
            "communication_ok": communication_state == 0,
            "emergency_stop": emergency_stop,
            "safety_stop": any(bool(int(item)) for item in safety),
            "safety_stop_inputs": [int(item) for item in safety],
            "enabled": enabled,
            "faulted": faulted,
            "main_code": main_code,
            "sub_code": sub_code,
            "robot_mode": None if robot_mode_raw is None else int(robot_mode_raw),
            "robot_state": None if robot_state_raw is None else int(robot_state_raw),
            "program_state": program_state,
            "safety_ok": safety_ok,
            "safe_to_move": safety_ok and program_state == 1,
            "sampled_at": _utc_now(),
        }

    def servo_start(self) -> None:
        method = self.robot.ServoMoveStart
        try:
            result = method(0)
        except TypeError:
            result = method()
        _error_code(result, "ServoMoveStart")

    def servo_target(
        self, q_deg: Sequence[float], *, period_s: float, command_id: int
    ) -> None:
        method = self.robot.ServoJ
        joints = list(map(float, q_deg))
        axes = [0.0] * 6
        try:
            result = method(
                joints,
                axes,
                cmdT=float(period_s),
                id=int(command_id),
                cmdType=0,
            )
        except TypeError:
            result = method(joints, axes, 0.0, 0.0, float(period_s), 0.0, 0.0)
        _error_code(result, "ServoJ")

    def servo_end(self) -> None:
        method = self.robot.ServoMoveEnd
        try:
            result = method(0)
        except TypeError:
            result = method()
        _error_code(result, "ServoMoveEnd")

    def stop_motion(self) -> None:
        errors: list[Exception] = []
        attempts = 0
        successes = 0
        for name in ("ImmStopJOG", "StopMotion"):
            method = getattr(self.robot, name, None)
            if method is None:
                continue
            attempts += 1
            try:
                _error_code(method(), name)
                successes += 1
            except Exception as exc:  # keep trying every available stop path
                errors.append(exc)
        if attempts == 0:
            raise Fr5BridgeError("the installed FAIRINO SDK provides no supported stop command")
        if successes == 0:
            raise Fr5BridgeError("all available FR5 stop commands failed")

    def upload_program(self, local_path: Path, remote_path: str) -> None:
        _error_code(self.robot.LuaUpload(str(local_path)), "LuaUpload")
        _error_code(self.robot.ProgramLoad(remote_path), "ProgramLoad")

    def set_speed(self, percent: int) -> None:
        _error_code(self.robot.SetSpeed(int(percent)), "SetSpeed")

    def start_joint_move(self, q_deg: Sequence[float], *, speed_percent: int) -> None:
        joints = list(map(float, q_deg))
        try:
            result = self.robot.MoveJ(
                joint_pos=joints,
                tool=0,
                user=0,
                vel=float(speed_percent),
                acc=100.0,
                ovl=100.0,
                blendT=1.0,
            )
        except TypeError as exc:
            raise Fr5BridgeError(
                "the installed FAIRINO SDK does not support guarded non-blocking MoveJ"
            ) from exc
        _error_code(result, "MoveJ to selected timeline pose")

    def run_program(self) -> None:
        _error_code(self.robot.ProgramRun(), "ProgramRun")

    def pause_motion(self) -> None:
        _error_code(self.robot.PauseMotion(), "PauseMotion")

    def resume_motion(self) -> None:
        _error_code(self.robot.ResumeMotion(), "ResumeMotion")

    def pause_program(self) -> None:
        method = getattr(self.robot, "ProgramPause", None)
        if method is None:
            self.pause_motion()
            return
        _error_code(method(), "ProgramPause")

    def resume_program(self) -> None:
        method = getattr(self.robot, "ProgramResume", None)
        if method is None:
            self.resume_motion()
            return
        _error_code(method(), "ProgramResume")

    def stop_program(self) -> None:
        _error_code(self.robot.ProgramStop(), "ProgramStop")

    def program_state(self) -> int:
        return int(_result_value(self.robot.GetProgramState(), "GetProgramState"))

    def current_program_line(self) -> int:
        method = getattr(self.robot, "GetCurrentLine", None)
        if method is None:
            raise Fr5BridgeError(
                "the installed FAIRINO SDK cannot confirm synchronized playback start"
            )
        value = _result_value(method(), "GetCurrentLine")
        if isinstance(value, bool):
            raise Fr5BridgeError("FR5 returned an invalid current program line")
        try:
            line = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise Fr5BridgeError("FR5 returned an invalid current program line") from exc
        if line < 0:
            raise Fr5BridgeError("FR5 returned an invalid current program line")
        return line


class Fr5LiveBridge:
    """Thread-safe FR5 connection, telemetry, jog, and playback coordinator."""

    def __init__(
        self,
        *,
        robot_ip: str = DEFAULT_ROBOT_IP,
        results_directory: str | os.PathLike[str] = "runs/fr5-live",
        adapter: RobotAdapter | None = None,
        allow_motion: bool = False,
        jog_speed_deg_s: float = DEFAULT_JOG_SPEED_DEG_S,
        playback_speed_percent: int = DEFAULT_PLAYBACK_SPEED_PERCENT,
        program_prefix: str = "/fruser/",
        clock: Any = time.monotonic,
    ) -> None:
        self.robot_ip = _private_ipv4(robot_ip)
        self.results_directory = _absolute_path(results_directory)
        self.runs_directory = self.results_directory / "runs"
        self.adapter = adapter or FairinoSdkAdapter()
        self.allow_motion = bool(allow_motion)
        self.jog_speed_rad_s = math.radians(float(jog_speed_deg_s))
        if not 0 < self.jog_speed_rad_s <= math.radians(20):
            raise ValueError("jog speed must be above 0 and no more than 20 deg/s")
        if (
            isinstance(playback_speed_percent, bool)
            or not 1 <= int(playback_speed_percent) <= 100
        ):
            raise ValueError("playback speed percent must be between 1 and 100")
        self.playback_speed_percent = int(playback_speed_percent)
        if not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*", program_prefix):
            raise ValueError("program prefix must be an absolute controller directory")
        self.program_prefix = program_prefix
        self._clock = clock
        self._lock = threading.RLock()
        self._robot_lock = threading.RLock()
        self._connected = False
        self._robot_snapshot: dict[str, object] | None = None
        self._robot_snapshot_at = 0.0
        self._connection_error: str | None = None
        self._telemetry_stop = threading.Event()
        self._telemetry_thread: threading.Thread | None = None
        self._servo_stop = threading.Event()
        self._servo_thread: threading.Thread | None = None
        self._servo_active = False
        self._servo_session_id: str | None = None
        self._servo_target: tuple[float, ...] | None = None
        self._servo_command: tuple[float, ...] | None = None
        self._servo_last_target_at = 0.0
        self._servo_last_sequence = 0
        self._servo_error: str | None = None
        self._run: dict[str, object] | None = None
        self._run_started_or_terminal = threading.Event()
        self._run_stop = threading.Event()
        self._run_active_since: float | None = None
        self._run_elapsed_seconds = 0.0
        self._run_stage_before_pause: str | None = None

    def health(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "ready",
            "robot_type": "fr5-wml-803",
            "configured_robot_ip": self.robot_ip,
            "motion_enabled": self.allow_motion,
            "speed_cap_percent": self.playback_speed_percent,
            "servo_period_s": SERVO_PERIOD_S,
            "current": self.snapshot(),
        }

    def _fresh_robot_snapshot(self) -> dict[str, object]:
        with self._lock:
            robot = dict(self._robot_snapshot or {})
            age = self._clock() - self._robot_snapshot_at
            error = self._connection_error
        if not robot:
            return {
                "safe_to_move": False,
                "fresh": False,
                "error": error or "waiting for measured robot state",
            }
        fresh = age <= TELEMETRY_FRESH_S
        robot["fresh"] = fresh
        robot["sample_age_ms"] = max(0.0, age * 1000.0)
        if not fresh:
            robot["safe_to_move"] = False
            robot["error"] = error or "measured robot state is stale"
        elif error:
            robot["safe_to_move"] = False
            robot["error"] = error
        return robot

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            connected = self._connected
            servo_active = self._servo_active
            servo_session_id = self._servo_session_id
            servo_error = self._servo_error
            run = dict(self._run) if self._run is not None else None
        robot = self._fresh_robot_snapshot() if connected else {}
        if run is not None:
            status = str(run["status"])
            result = run
        elif servo_active:
            status = "servo"
            result = {}
        else:
            status = "connected" if connected else "disconnected"
            result = {}
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": status,
            "connected": connected,
            "configured_robot_ip": self.robot_ip,
            "motion_enabled": self.allow_motion,
            "speed_cap_percent": self.playback_speed_percent,
            "robot": robot,
            **result,
        }
        if servo_active and servo_session_id is not None:
            payload["servo_session_id"] = servo_session_id
        if servo_error and status != "running":
            payload["error"] = servo_error
        return payload

    def connect(self, payload: Mapping[str, object]) -> dict[str, object]:
        unexpected = set(payload) - {"robot_ip"}
        if unexpected:
            raise Fr5BridgeError(f"unsupported connect field: {sorted(unexpected)[0]}")
        requested = payload.get("robot_ip")
        if requested is not None and not isinstance(requested, str):
            raise Fr5BridgeError("robot IP must be text")
        if requested is not None and _private_ipv4(requested) != self.robot_ip:
            raise Fr5BridgeError(
                "the requested robot does not match the address configured when the helper started"
            )
        with self._lock:
            if self._connected:
                return self.snapshot()
        try:
            with self._robot_lock:
                self.adapter.connect(self.robot_ip)
                measured = self.adapter.read_state()
        except Exception:
            with self._robot_lock:
                self.adapter.close()
            raise
        with self._lock:
            self._connected = True
            self._robot_snapshot = measured
            self._robot_snapshot_at = self._clock()
            self._connection_error = None
            self._servo_error = None
            if self._run is not None and self._run.get("status") in TERMINAL_RUN_STATES:
                self._run = None
        self._start_telemetry()
        return self.snapshot()

    def _start_telemetry(self) -> None:
        with self._lock:
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                return
            self._telemetry_stop.clear()
            self._telemetry_thread = threading.Thread(
                target=self._telemetry_loop,
                name="fr5-live-telemetry",
                daemon=True,
            )
            self._telemetry_thread.start()

    def _telemetry_loop(self) -> None:
        failures = 0
        communication_stop_issued = False
        while not self._telemetry_stop.wait(0.1):
            try:
                with self._robot_lock:
                    measured = self.adapter.read_state()
                with self._lock:
                    if not self._connected:
                        return
                    self._robot_snapshot = measured
                    self._robot_snapshot_at = self._clock()
                    self._connection_error = None
                    servo_active = self._servo_active
                    run_active = self._run is not None and self._run.get("status") not in TERMINAL_RUN_STATES
                failures = 0
                communication_stop_issued = False
                safety_ok = measured.get("safety_ok", measured.get("safe_to_move")) is True
                servo_program_stopped = not servo_active or measured.get("program_state") == 1
                if (servo_active or run_active) and (not safety_ok or not servo_program_stopped):
                    self._servo_stop.set()
                    self._run_stop.set()
                    with self._lock:
                        self._connection_error = "FR5 safety state changed during physical motion"
                    try:
                        with self._robot_lock:
                            if run_active:
                                self.adapter.stop_program()
                            self.adapter.stop_motion()
                    except Exception:
                        pass
            except Exception as exc:
                failures += 1
                with self._lock:
                    self._connection_error = f"{type(exc).__name__}: {exc}"
                    servo_active = self._servo_active
                    run_active = self._run is not None and self._run.get("status") not in TERMINAL_RUN_STATES
                if failures >= 3 and (servo_active or run_active):
                    self._servo_stop.set()
                    self._run_stop.set()
                    if not communication_stop_issued:
                        communication_stop_issued = True
                        try:
                            with self._robot_lock:
                                if run_active:
                                    self.adapter.stop_program()
                                self.adapter.stop_motion()
                        except Exception:
                            pass

    def _require_connected(self) -> dict[str, object]:
        with self._lock:
            if not self._connected:
                raise Fr5BridgeError("connect the FR5 first")
        robot = self._fresh_robot_snapshot()
        if robot.get("fresh") is not True:
            raise Fr5BridgeError(str(robot.get("error") or "FR5 state is not fresh"))
        return robot

    def _require_motion_ready(self) -> dict[str, object]:
        if not self.allow_motion:
            raise Fr5BridgeError(
                "this helper was started read-only; restart it with --allow-motion"
            )
        self._require_connected()
        with self._robot_lock:
            robot = self.adapter.read_state()
        with self._lock:
            self._robot_snapshot = robot
            self._robot_snapshot_at = self._clock()
            self._connection_error = None
        if robot.get("safe_to_move") is not True:
            raise Fr5BridgeError(str(robot.get("error") or "FR5 is not safe to move"))
        return robot

    def begin_servo(self, payload: Mapping[str, object]) -> dict[str, object]:
        if payload:
            raise Fr5BridgeError("live-control start does not accept fields")
        robot = self._require_motion_ready()
        with self._lock:
            if self._run is not None and self._run.get("status") not in TERMINAL_RUN_STATES:
                raise Fr5BridgeConflict("physical playback is already active")
            if self._run is not None and self._run.get("status") in TERMINAL_RUN_STATES:
                self._run = None
            if self._servo_active:
                return self.snapshot()
            q = _finite_vector(robot.get("actual_q_rad"), field="measured FR5 pose")
        with self._robot_lock:
            self.adapter.servo_start()
        with self._lock:
            self._servo_active = True
            self._servo_session_id = uuid.uuid4().hex
            self._servo_target = q
            self._servo_command = q
            self._servo_last_target_at = self._clock()
            self._servo_last_sequence = 0
            self._servo_error = None
            self._servo_stop.clear()
            self._servo_thread = threading.Thread(
                target=self._servo_loop,
                name="fr5-live-servo",
                daemon=True,
            )
            self._servo_thread.start()
        return self.snapshot()

    def set_servo_target(self, payload: Mapping[str, object]) -> dict[str, object]:
        if set(payload) != {"servo_session_id", "sequence", "q_rad"}:
            raise Fr5BridgeError(
                "live-control target requires session, sequence, and six joints"
            )
        q = _finite_vector(payload["q_rad"], field="FR5 live target")
        for index, (value, domain) in enumerate(zip(q, JOINT_DOMAIN_RAD, strict=True)):
            if not domain[0] <= value <= domain[1]:
                raise Fr5BridgeError(f"FR5 live target J{index + 1} is outside its allowed range")
        sequence = payload["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise Fr5BridgeError("live-control sequence must be a positive integer")
        with self._lock:
            if (
                not isinstance(payload["servo_session_id"], str)
                or not self._servo_active
                or payload["servo_session_id"] != self._servo_session_id
            ):
                raise Fr5BridgeConflict("live-control session is no longer active")
            if sequence <= self._servo_last_sequence:
                raise Fr5BridgeConflict("live-control target is stale")
            self._servo_last_sequence = sequence
            self._servo_target = q
            self._servo_last_target_at = self._clock()
        return self.snapshot()

    def _servo_loop(self) -> None:
        command_id = 0
        next_deadline = self._clock()
        emergency_stop = False
        error: str | None = None
        try:
            while not self._servo_stop.is_set():
                now = self._clock()
                with self._lock:
                    if now - self._servo_last_target_at > SERVO_WATCHDOG_S:
                        emergency_stop = True
                        error = "live pose control timed out and was stopped"
                        break
                    target = self._servo_target
                    command = self._servo_command
                if target is None or command is None:
                    raise Fr5BridgeError("live-control state is incomplete")
                max_step = self.jog_speed_rad_s * SERVO_PERIOD_S
                next_command = tuple(
                    current + max(-max_step, min(max_step, wanted - current))
                    for current, wanted in zip(command, target, strict=True)
                )
                with self._robot_lock:
                    self.adapter.servo_target(
                        [math.degrees(value) for value in next_command],
                        period_s=SERVO_PERIOD_S,
                        command_id=command_id,
                    )
                command_id = (command_id + 1) % 2_147_483_647
                with self._lock:
                    self._servo_command = next_command
                next_deadline += SERVO_PERIOD_S
                if self._clock() > next_deadline:
                    next_deadline = self._clock() + SERVO_PERIOD_S
                self._servo_stop.wait(max(0.0, next_deadline - self._clock()))
        except Exception as exc:
            emergency_stop = True
            error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                with self._robot_lock:
                    self.adapter.servo_end()
                    if emergency_stop:
                        self.adapter.stop_motion()
            except Exception as exc:
                error = error or f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._servo_active = False
                self._servo_session_id = None
                self._servo_target = None
                self._servo_command = None
                self._servo_error = error

    def end_servo(self, payload: Mapping[str, object]) -> dict[str, object]:
        unexpected = set(payload) - {"servo_session_id"}
        if unexpected:
            raise Fr5BridgeError(f"unsupported live-control stop field: {sorted(unexpected)[0]}")
        with self._lock:
            session = self._servo_session_id
            active = self._servo_active
        supplied = payload.get("servo_session_id")
        if supplied is not None and not isinstance(supplied, str):
            raise Fr5BridgeError("live-control stop session must be text")
        if supplied is not None and session is not None and supplied != session:
            raise Fr5BridgeConflict("live-control stop session does not match")
        if active:
            self._servo_stop.set()
            thread = self._servo_thread
            if thread is not None:
                thread.join(timeout=1.0)
            with self._lock:
                still_active = self._servo_active
            if still_active:
                with self._robot_lock:
                    self.adapter.stop_motion()
                raise Fr5BridgeError("FR5 live-control thread did not stop promptly")
        return self.snapshot()

    @staticmethod
    def _decode_trajectory(
        value: object,
        *,
        expected_frames: int,
        initial_q: Sequence[float],
    ) -> tuple[str, str]:
        if not isinstance(value, Mapping):
            raise Fr5BridgeError("physical playback requires a numeric trajectory")
        required = {"schema_version", "encoding", "frame_count", "payload_base64"}
        if set(value) != required:
            raise Fr5BridgeError("physical playback trajectory has an invalid shape")
        if value["schema_version"] != 1 or value["encoding"] != TRAJECTORY_ENCODING:
            raise Fr5BridgeError("physical playback trajectory format is unsupported")
        frame_count = value["frame_count"]
        if (
            isinstance(frame_count, bool)
            or not isinstance(frame_count, int)
            or frame_count < 1
            or frame_count > MAX_TRAJECTORY_FRAMES
            or frame_count != expected_frames
        ):
            raise Fr5BridgeError("physical playback trajectory frame count is invalid")
        encoded = value["payload_base64"]
        if not isinstance(encoded, str) or not encoded:
            raise Fr5BridgeError("physical playback trajectory payload is invalid")
        maximum_encoded = ((frame_count * TRAJECTORY_RECORD_BYTES + 2) // 3) * 4
        if len(encoded) != maximum_encoded:
            raise Fr5BridgeError("physical playback trajectory payload length is invalid")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise Fr5BridgeError("physical playback trajectory payload is not valid Base64") from exc
        if len(raw) != frame_count * TRAJECTORY_RECORD_BYTES:
            raise Fr5BridgeError("physical playback trajectory record count is invalid")

        absolute_microdegrees = [0] * 6
        previous_q = tuple(float(item) for item in initial_q)
        previous_velocity = (0.0,) * 6
        speed_tolerance = math.radians(0.01)
        acceleration_tolerance = math.radians(0.2)
        for frame in range(frame_count):
            offset = frame * TRAJECTORY_RECORD_BYTES
            q: list[float] = []
            for joint in range(6):
                start = offset + joint * 4
                delta = int.from_bytes(raw[start : start + 4], "big", signed=True)
                absolute_microdegrees[joint] += delta
                radians = math.radians(
                    absolute_microdegrees[joint] * TRAJECTORY_SCALE_DEG
                )
                domain = JOINT_DOMAIN_RAD[joint]
                if not domain[0] <= radians <= domain[1]:
                    raise Fr5BridgeError(
                        f"physical trajectory frame {frame + 1} J{joint + 1} is outside its allowed range"
                    )
                q.append(radians)
            duration_us = int.from_bytes(raw[offset + 24 : offset + 28], "big")
            if duration_us != round(SERVO_PERIOD_S * 1_000_000):
                raise Fr5BridgeError(
                    f"physical trajectory frame {frame + 1} does not use the required command period"
                )
            velocity = tuple(
                (position - previous) / SERVO_PERIOD_S
                for position, previous in zip(q, previous_q, strict=True)
            )
            for joint, measured in enumerate(velocity):
                if abs(measured) > JOINT_MAX_SPEED_RAD_S[joint] + speed_tolerance:
                    raise Fr5BridgeError(
                        f"physical trajectory frame {frame + 1} exceeds the J{joint + 1} speed limit"
                    )
                acceleration = (measured - previous_velocity[joint]) / SERVO_PERIOD_S
                if (
                    abs(acceleration)
                    > JOINT_MAX_ACCELERATION_RAD_S2[joint] + acceleration_tolerance
                ):
                    raise Fr5BridgeError(
                        f"physical trajectory frame {frame + 1} exceeds the J{joint + 1} acceleration limit"
                    )
            previous_q = tuple(q)
            previous_velocity = velocity
        return encoded, hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _physical_program_source(
        *,
        encoded_trajectory: str,
        trajectory_sha256: str,
        frame_count: int,
        initial_q: Sequence[float],
        project_name: str,
    ) -> str:
        safe_project = re.sub(r"[^\x20-\x7e]", "_", project_name).replace("\n", " ")
        setup = ", ".join(f"{math.degrees(value):.12f}" for value in initial_q)
        chunks = [
            encoded_trajectory[index : index + 2048]
            for index in range(0, len(encoded_trajectory), 2048)
        ]
        lines = [
            "-- FAIRINO FR5-WML model 803 guarded physical choreography.",
            f"-- Program: {safe_project}",
            f"-- Profile fingerprint: {PROFILE_FINGERPRINT}",
            f"-- Trajectory SHA-256: {trajectory_sha256}",
            "-- Generated locally by the FR5 safety helper; browser Lua is never executed.",
            "-- PRE-ROLL: excluded from the choreography clock.",
            f"wx, wy, wz, wrx, wry, wrz = GetForwardKin({setup})",
            f"MoveJ({{{setup}}}, {{wx, wy, wz, wrx, wry, wrz}}, 0, 0, 20, 0, 20, {{0, 0, 0, 0}}, -1, 0, {{0, 0, 0, 0, 0, 0}})",
            "-- CHOREOGRAPHY CLOCK START",
            "local DATA = table.concat({",
            *(f'  "{chunk}",' for chunk in chunks),
            "})",
            'local ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"',
            "local DECODE = {}",
            "for i = 1, #ALPHABET do DECODE[string.byte(ALPHABET, i)] = i - 1 end",
            "local data_pos, accumulator, bit_count = 1, 0, 0",
            "local function next_value()",
            "  while data_pos <= #DATA do",
            "    local code = string.byte(DATA, data_pos)",
            "    data_pos = data_pos + 1",
            "    local value = DECODE[code]",
            "    if value ~= nil then return value end",
            "  end",
            "  return nil",
            "end",
            "local function read_u8()",
            "  while bit_count < 8 do",
            "    local value = next_value()",
            '    if value == nil then error("Unexpected end of trajectory payload") end',
            "    accumulator = accumulator * 64 + value",
            "    bit_count = bit_count + 6",
            "  end",
            "  bit_count = bit_count - 8",
            "  local divisor = 2 ^ bit_count",
            "  local value = math.floor(accumulator / divisor)",
            "  accumulator = accumulator - value * divisor",
            "  return value",
            "end",
            "local function read_u32()",
            "  return read_u8() * 16777216 + read_u8() * 65536 + read_u8() * 256 + read_u8()",
            "end",
            "local function read_i32()",
            "  local value = read_u32()",
            "  if value >= 2147483648 then return value - 4294967296 end",
            "  return value",
            "end",
            "local q = {0, 0, 0, 0, 0, 0}",
            f"local SCALE = {TRAJECTORY_SCALE_DEG:.6f}",
            f"for frame = 1, {frame_count} do",
            "  for joint = 1, 6 do q[joint] = q[joint] + read_i32() end",
            "  local command_time = read_u32() / 1000000",
            "  ServoJ(q[1] * SCALE, q[2] * SCALE, q[3] * SCALE, q[4] * SCALE, q[5] * SCALE, q[6] * SCALE, 0, 0, 0, 0, 100, 100, command_time, 0, 0)",
            "end",
            "",
        ]
        source = "\n".join(lines)
        try:
            source_bytes = source.encode("ascii")
        except UnicodeEncodeError as exc:
            raise Fr5BridgeError("generated physical program is not ASCII") from exc
        if len(source_bytes) > MAX_LUA_BYTES:
            raise Fr5BridgeError("physical choreography is too large for guarded upload")
        return source

    def _validate_play(self, payload: Mapping[str, object]) -> dict[str, object]:
        required = {
            "request_id",
            "initial_q",
            "expected_steps",
            "expected_seconds",
            "timeline_start_seconds",
            "timeline_end_seconds",
            "project_name",
            "trajectory",
            "filename",
            "profile_fingerprint",
            "operator_confirmed",
        }
        if set(payload) != required:
            missing = sorted(required - set(payload))
            unexpected = sorted(set(payload) - required)
            detail = f"missing {missing[0]}" if missing else f"unsupported {unexpected[0]}"
            raise Fr5BridgeError(f"physical playback request is invalid: {detail}")
        if payload["operator_confirmed"] is not True:
            raise Fr5BridgeError("operator confirmation is required for physical playback")
        if payload["profile_fingerprint"] != PROFILE_FINGERPRINT:
            raise Fr5BridgeError("FR5 motion profile does not match this helper")
        if not isinstance(payload["request_id"], str):
            raise Fr5BridgeError("physical playback request ID must be text")
        request_id = payload["request_id"]
        if not re.fullmatch(r"[A-Za-z0-9-]{8,80}", request_id, re.ASCII):
            raise Fr5BridgeError("physical playback request ID is invalid")
        initial_q = _finite_vector(payload["initial_q"], field="FR5 starting pose")
        for index, (value, domain) in enumerate(
            zip(initial_q, JOINT_DOMAIN_RAD, strict=True)
        ):
            if not domain[0] <= value <= domain[1]:
                raise Fr5BridgeError(f"FR5 starting pose J{index + 1} is outside its allowed range")
        steps = payload["expected_steps"]
        if isinstance(steps, bool) or not isinstance(steps, int) or steps <= 0:
            raise Fr5BridgeError("physical playback duration is invalid")
        if isinstance(payload["expected_seconds"], bool):
            raise Fr5BridgeError("physical playback duration must be numeric")
        try:
            seconds = float(payload["expected_seconds"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise Fr5BridgeError("physical playback duration must be numeric") from exc
        if not math.isfinite(seconds) or seconds <= 0 or not math.isclose(
            seconds, steps * SERVO_PERIOD_S, abs_tol=1e-9
        ):
            raise Fr5BridgeError("physical playback duration does not match the FR5 command clock")
        if isinstance(payload["timeline_start_seconds"], bool) or isinstance(
            payload["timeline_end_seconds"], bool
        ):
            raise Fr5BridgeError("physical playback timeline position must be numeric")
        try:
            timeline_start = float(payload["timeline_start_seconds"])
            timeline_end = float(payload["timeline_end_seconds"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise Fr5BridgeError("physical playback timeline position must be numeric") from exc
        if (
            not math.isfinite(timeline_start)
            or not math.isfinite(timeline_end)
            or timeline_start < 0
            or not math.isclose(
                timeline_start / SERVO_PERIOD_S,
                round(timeline_start / SERVO_PERIOD_S),
                abs_tol=1e-7,
            )
            or not math.isclose(timeline_end - timeline_start, seconds, abs_tol=1e-7)
        ):
            raise Fr5BridgeError("physical playback timeline range is invalid")
        if not isinstance(payload["filename"], str):
            raise Fr5BridgeError("physical playback filename must be text")
        filename = payload["filename"]
        if _PROGRAM_NAME.fullmatch(filename) is None:
            raise Fr5BridgeError("physical playback filename is invalid")
        if not isinstance(payload["project_name"], str):
            raise Fr5BridgeError("physical playback project name must be text")
        project_name = payload["project_name"].strip()
        if not project_name or len(project_name) > 120:
            raise Fr5BridgeError("physical playback project name is invalid")
        encoded_trajectory, trajectory_sha256 = self._decode_trajectory(
            payload["trajectory"],
            expected_frames=steps,
            initial_q=initial_q,
        )
        lua_source = self._physical_program_source(
            encoded_trajectory=encoded_trajectory,
            trajectory_sha256=trajectory_sha256,
            frame_count=steps,
            initial_q=initial_q,
            project_name=project_name,
        )
        try:
            clock_sync_line = next(
                line_number
                for line_number, line in enumerate(lua_source.splitlines(), start=1)
                if line.strip() == "local command_time = read_u32() / 1000000"
            )
        except StopIteration as exc:
            raise Fr5BridgeError(
                "generated physical choreography has no synchronized start marker"
            ) from exc
        return {
            "request_id": request_id,
            "initial_q": initial_q,
            "expected_steps": steps,
            "expected_seconds": seconds,
            "timeline_start_seconds": timeline_start,
            "timeline_end_seconds": timeline_end,
            "filename": filename,
            "project_name": project_name,
            "trajectory_sha256": trajectory_sha256,
            "lua_source": lua_source,
            "clock_sync_line": clock_sync_line,
        }

    def start_playback(self, payload: Mapping[str, object]) -> dict[str, object]:
        values = self._validate_play(payload)
        robot = self._require_motion_ready()
        with self._lock:
            if self._servo_active:
                raise Fr5BridgeConflict("release the live pose control before Play")
            if self._run is not None and self._run.get("status") not in TERMINAL_RUN_STATES:
                raise Fr5BridgeConflict("physical playback is already active")
        actual_q = _finite_vector(robot.get("actual_q_rad"), field="measured FR5 pose")
        actual_qd = _finite_vector(
            robot.get("actual_qd_rad_s"), field="measured FR5 joint speed"
        )
        error = max(
            abs(actual - expected)
            for actual, expected in zip(actual_q, values["initial_q"], strict=True)  # type: ignore[arg-type]
        )
        if max(map(abs, actual_qd)) > STATIONARY_TOLERANCE_RAD_S:
            raise Fr5BridgeError("FR5 must be stationary before Play")

        run_id = uuid.uuid4().hex
        run = {
            "run_id": run_id,
            "request_id": values["request_id"],
            "status": "arming",
            "program_name": values["filename"],
            "project_name": values["project_name"],
            "expected_steps": values["expected_steps"],
            "expected_seconds": values["expected_seconds"],
            "timeline_start_seconds": values["timeline_start_seconds"],
            "timeline_end_seconds": values["timeline_end_seconds"],
            "controller_period_s": SERVO_PERIOD_S,
            "trajectory_sha256": values["trajectory_sha256"],
            "speed_cap_percent": self.playback_speed_percent,
            "created_at": _utc_now(),
            "elapsed_seconds": 0.0,
            "complete": False,
            "program_state_edge_complete": False,
            "clock_sync_confirmed": False,
            "positioned_to_start": error > START_TOLERANCE_RAD,
            "error": None,
        }
        with self._lock:
            self._run = run
            self._run_elapsed_seconds = 0.0
            self._run_active_since = None
            self._run_stage_before_pause = None
            self._run_started_or_terminal.clear()
            self._run_stop.clear()
        thread = threading.Thread(
            target=self._playback_loop,
            args=(run_id, values),
            name=f"fr5-live-playback-{run_id}",
            daemon=True,
        )
        thread.start()
        self._run_started_or_terminal.wait(timeout=17.0)
        snapshot = self.snapshot()
        if snapshot["status"] not in {"positioning", "running", "paused"}:
            raise Fr5BridgeError(str(snapshot.get("error") or "FR5 did not start the program"))
        return snapshot

    def _playback_loop(self, run_id: str, values: Mapping[str, object]) -> None:
        run_dir = self.runs_directory / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            local_path = run_dir / str(values["filename"])
            source = str(values["lua_source"])
            local_path.write_text(source, encoding="ascii", newline="\n")
            source_sha256 = hashlib.sha256(source.encode("ascii")).hexdigest()
            remote_path = f"{self.program_prefix}{values['filename']}"
            with self._robot_lock:
                self.adapter.upload_program(local_path, remote_path)
                self.adapter.set_speed(self.playback_speed_percent)
                measured = self.adapter.read_state()
                if measured.get("safe_to_move") is not True:
                    raise Fr5BridgeError("FR5 safety or program state changed before Play")
                actual_q = _finite_vector(
                    measured.get("actual_q_rad"), field="measured FR5 pose"
                )
                actual_qd = _finite_vector(
                    measured.get("actual_qd_rad_s"), field="measured FR5 joint speed"
                )
                start_error = max(
                    abs(actual - expected)
                    for actual, expected in zip(
                        actual_q, values["initial_q"], strict=True  # type: ignore[arg-type]
                    )
                )
                if max(map(abs, actual_qd)) > STATIONARY_TOLERANCE_RAD_S:
                    raise Fr5BridgeError("FR5 began moving before Play")
                with self._lock:
                    self._robot_snapshot = measured
                    self._robot_snapshot_at = self._clock()
                    self._connection_error = None

                if start_error > START_TOLERANCE_RAD:
                    with self._lock:
                        if self._run is not None and self._run.get("run_id") == run_id:
                            self._run.update(
                                {
                                    "status": "positioning",
                                    "source_sha256": source_sha256,
                                    "remote_program_path": remote_path,
                                }
                            )
                    self._run_started_or_terminal.set()
                    self.adapter.start_joint_move(
                        [math.degrees(value) for value in values["initial_q"]],  # type: ignore[arg-type]
                        speed_percent=self.playback_speed_percent,
                    )

            if start_error > START_TOLERANCE_RAD:
                position_deadline = self._clock() + 180.0
                while self._clock() < position_deadline:
                    if self._run_stop.is_set():
                        raise InterruptedError("FR5 playback was stopped while moving to the selected pose")
                    with self._lock:
                        paused = self._run is not None and self._run.get("status") == "paused"
                    if paused:
                        time.sleep(0.05)
                        continue
                    with self._robot_lock:
                        measured = self.adapter.read_state()
                    if measured.get("safety_ok") is not True:
                        raise Fr5BridgeError("FR5 safety state changed while moving to the selected pose")
                    actual_q = _finite_vector(
                        measured.get("actual_q_rad"), field="measured FR5 pose"
                    )
                    actual_qd = _finite_vector(
                        measured.get("actual_qd_rad_s"), field="measured FR5 joint speed"
                    )
                    start_error = max(
                        abs(actual - expected)
                        for actual, expected in zip(
                            actual_q, values["initial_q"], strict=True  # type: ignore[arg-type]
                        )
                    )
                    with self._lock:
                        self._robot_snapshot = measured
                        self._robot_snapshot_at = self._clock()
                    if (
                        start_error <= START_TOLERANCE_RAD
                        and max(map(abs, actual_qd)) <= STATIONARY_TOLERANCE_RAD_S
                        and measured.get("program_state") == 1
                    ):
                        break
                    time.sleep(0.05)
                else:
                    raise Fr5BridgeError("FR5 did not reach the selected timeline pose in time")

            with self._robot_lock:
                measured = self.adapter.read_state()
                if measured.get("safe_to_move") is not True:
                    raise Fr5BridgeError("FR5 safety or program state changed before playback")
                actual_q = _finite_vector(
                    measured.get("actual_q_rad"), field="measured FR5 pose"
                )
                actual_qd = _finite_vector(
                    measured.get("actual_qd_rad_s"), field="measured FR5 joint speed"
                )
                start_error = max(
                    abs(actual - expected)
                    for actual, expected in zip(
                        actual_q, values["initial_q"], strict=True  # type: ignore[arg-type]
                    )
                )
                if start_error > START_TOLERANCE_RAD:
                    raise Fr5BridgeError("FR5 moved away from the selected timeline pose before playback")
                if max(map(abs, actual_qd)) > STATIONARY_TOLERANCE_RAD_S:
                    raise Fr5BridgeError("FR5 began moving before playback")
                self.adapter.run_program()

            start_deadline = self._clock() + 15.0
            while self._clock() < start_deadline and not self._run_stop.is_set():
                with self._robot_lock:
                    program_state = self.adapter.program_state()
                    current_line = (
                        self.adapter.current_program_line()
                        if program_state == 2
                        else 0
                    )
                # FAIRINO reports ProgramRun before the Lua preamble has reached
                # the first trajectory frame. GetCurrentLine is the controller
                # acknowledgement that frame dispatch is actually beginning.
                # Accept one line before the 1-based marker for SDK releases
                # that expose the controller's line counter as zero-based.
                clock_sync_line = int(values["clock_sync_line"])
                if program_state == 2 and current_line >= clock_sync_line - 1:
                    now = self._clock()
                    with self._lock:
                        if self._run is not None and self._run.get("run_id") == run_id:
                            self._run_elapsed_seconds = 0.0
                            self._run_active_since = now
                            self._run.update(
                                {
                                    "status": "running",
                                    "started_at": _utc_now(),
                                    "source_sha256": source_sha256,
                                    "remote_program_path": remote_path,
                                    "clock_sync_confirmed": True,
                                    "clock_sync_line": clock_sync_line,
                                    "controller_program_line": current_line,
                                }
                            )
                    self._run_started_or_terminal.set()
                    break
                time.sleep(0.05)
            with self._lock:
                program_started = self._run_active_since is not None
            if not program_started:
                if self._run_stop.is_set():
                    raise InterruptedError("FR5 playback was stopped before motion began")
                raise Fr5BridgeError("FR5 did not report a running program")

            while not self._run_stop.is_set():
                with self._robot_lock:
                    program_state = self.adapter.program_state()
                with self._lock:
                    if self._run is not None and self._run.get("run_id") == run_id:
                        status = str(self._run.get("status"))
                        now = self._clock()
                        if status == "running" and self._run_active_since is not None:
                            self._run_elapsed_seconds += max(0.0, now - self._run_active_since)
                            self._run_active_since = now
                            self._run["elapsed_seconds"] = self._run_elapsed_seconds
                        self._run["program_state"] = program_state
                        if program_state == 3 and status == "running":
                            self._run["status"] = "paused"
                            self._run_stage_before_pause = "running"
                            self._run_active_since = None
                            status = "paused"
                    else:
                        status = "failed"
                if status == "paused":
                    time.sleep(0.05)
                    continue
                if program_state == 1:
                    with self._lock:
                        if self._run is not None and self._run.get("run_id") == run_id:
                            self._run.update(
                                {
                                    "status": "completed",
                                    "completed_at": _utc_now(),
                                    "duration_seconds": self._run_elapsed_seconds,
                                    "complete": True,
                                    "program_state_edge_complete": True,
                                }
                            )
                    break
                if program_state not in {2, 3}:
                    raise Fr5BridgeError(f"FR5 reported unexpected program state {program_state}")
                time.sleep(0.05)
            if self._run_stop.is_set():
                with self._lock:
                    if self._run is not None and self._run.get("run_id") == run_id:
                        self._run.update(
                            {
                                "status": "aborted",
                                "completed_at": _utc_now(),
                                "duration_seconds": self._run_elapsed_seconds,
                                "error": "physical playback was stopped",
                            }
                        )
        except InterruptedError as exc:
            with self._lock:
                if self._run is not None and self._run.get("run_id") == run_id:
                    self._run.update(
                        {"status": "aborted", "completed_at": _utc_now(), "error": str(exc)}
                    )
        except Exception as exc:
            try:
                with self._robot_lock:
                    self.adapter.stop_program()
                    self.adapter.stop_motion()
            except Exception:
                pass
            with self._lock:
                if self._run is not None and self._run.get("run_id") == run_id:
                    self._run.update(
                        {
                            "status": "failed",
                            "completed_at": _utc_now(),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        finally:
            self._run_started_or_terminal.set()
            with self._lock:
                result = dict(self._run or {})
            if result and result.get("run_id") == run_id:
                try:
                    run_dir.mkdir(parents=True, exist_ok=True)
                    (run_dir / "result.json").write_text(
                        json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
                        + "\n",
                        encoding="utf-8",
                    )
                except Exception:
                    pass

    def pause(self, payload: Mapping[str, object]) -> dict[str, object]:
        if payload:
            raise Fr5BridgeError("pause does not accept fields")
        with self._lock:
            if self._run is None or self._run.get("status") not in {"positioning", "running"}:
                raise Fr5BridgeConflict("FR5 playback is not currently pausable")
            run_id = self._run.get("run_id")
            stage = str(self._run.get("status"))
        with self._robot_lock:
            if stage == "positioning":
                self.adapter.pause_motion()
            else:
                self.adapter.pause_program()
        with self._lock:
            if self._run is None or self._run.get("run_id") != run_id:
                raise Fr5BridgeConflict("FR5 playback changed before Pause completed")
            if stage == "running" and self._run_active_since is not None:
                now = self._clock()
                self._run_elapsed_seconds += max(0.0, now - self._run_active_since)
                self._run_active_since = None
                self._run["elapsed_seconds"] = self._run_elapsed_seconds
            self._run_stage_before_pause = stage
            self._run["status"] = "paused"
            self._run["paused_at"] = _utc_now()
        return self.snapshot()

    def resume(self, payload: Mapping[str, object]) -> dict[str, object]:
        if payload:
            raise Fr5BridgeError("resume does not accept fields")
        self._require_connected()
        with self._lock:
            if self._run is None or self._run.get("status") != "paused":
                raise Fr5BridgeConflict("FR5 playback is not paused")
            run_id = self._run.get("run_id")
            stage = self._run_stage_before_pause
            if stage not in {"positioning", "running"}:
                raise Fr5BridgeConflict("FR5 pause state cannot be resumed")
        with self._robot_lock:
            measured = self.adapter.read_state()
            if measured.get("safety_ok") is not True:
                raise Fr5BridgeError("FR5 safety state does not allow Resume")
            if stage == "positioning":
                self.adapter.resume_motion()
            else:
                self.adapter.resume_program()
        with self._lock:
            if self._run is None or self._run.get("run_id") != run_id:
                raise Fr5BridgeConflict("FR5 playback changed before Resume completed")
            self._run["status"] = stage
            self._run["resumed_at"] = _utc_now()
            if stage == "running":
                self._run_active_since = self._clock()
        return self.snapshot()

    def stop(self, payload: Mapping[str, object]) -> dict[str, object]:
        if payload:
            raise Fr5BridgeError("stop does not accept fields")
        self._servo_stop.set()
        self._run_stop.set()
        with self._lock:
            servo_thread = self._servo_thread if self._servo_active else None
            run_active = self._run is not None and self._run.get("status") not in TERMINAL_RUN_STATES
            run_stage = self._run_stage_before_pause if self._run is not None and self._run.get("status") == "paused" else (
                str(self._run.get("status")) if self._run is not None else None
            )
        errors: list[Exception] = []
        if self._connected:
            try:
                with self._robot_lock:
                    if run_active and run_stage != "positioning":
                        self.adapter.stop_program()
                    self.adapter.stop_motion()
            except Exception as exc:
                errors.append(exc)
        if servo_thread is not None:
            servo_thread.join(timeout=1.0)
        if errors:
            raise Fr5BridgeError(f"FR5 stop command failed: {errors[0]}")
        if run_active:
            with self._lock:
                if self._run is not None and self._run.get("status") not in TERMINAL_RUN_STATES:
                    if self._run.get("status") == "running" and self._run_active_since is not None:
                        self._run_elapsed_seconds += max(0.0, self._clock() - self._run_active_since)
                    self._run_active_since = None
                    self._run.update(
                        {
                            "status": "aborted",
                            "completed_at": _utc_now(),
                            "duration_seconds": self._run_elapsed_seconds,
                            "error": "physical playback was stopped",
                        }
                    )
            self._run_started_or_terminal.set()
        return self.snapshot()

    def disconnect(self, payload: Mapping[str, object]) -> dict[str, object]:
        if payload:
            raise Fr5BridgeError("disconnect does not accept fields")
        with self._lock:
            motion_active = self._servo_active or (
                self._run is not None
                and self._run.get("status") not in TERMINAL_RUN_STATES
            )
        if motion_active:
            self.stop({})
        else:
            self._servo_stop.set()
            self._run_stop.set()
        self._telemetry_stop.set()
        thread = self._telemetry_thread
        if thread is not None:
            thread.join(timeout=1.0)
        with self._robot_lock:
            self.adapter.close()
        with self._lock:
            self._connected = False
            self._robot_snapshot = None
            self._connection_error = None
            self._servo_error = None
            self._run = None
        return self.snapshot()

    def close(self) -> None:
        try:
            if self._connected:
                self.disconnect({})
        except Exception:
            try:
                with self._robot_lock:
                    self.adapter.close()
            except Exception:
                pass


class _BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        bridge: Fr5LiveBridge,
        *,
        frontend_path: Path,
    ) -> None:
        super().__init__(address, Fr5LiveHandler)
        self.bridge = bridge
        self.frontend_path = frontend_path
        self.frontend_bytes = frontend_path.read_bytes()
        if (
            len(self.frontend_bytes) > 32 * 1024 * 1024
            or not self.frontend_bytes.lstrip().lower().startswith(b"<!doctype html>")
            or PROFILE_FINGERPRINT.encode("ascii") not in self.frontend_bytes
        ):
            raise Fr5BridgeError("trusted choreography frontend is invalid")
        self.frontend_sha256 = hashlib.sha256(self.frontend_bytes).hexdigest()
        self.expected_host = f"127.0.0.1:{self.server_address[1]}"
        self.expected_origin = f"http://{self.expected_host}"
        self.session_token = secrets.token_urlsafe(18)
        self._pairing_lock = threading.Lock()
        self.paired_web_origin: str | None = None
        self._rate_lock = threading.Lock()
        self._mutation_events: dict[str, deque[float]] = {
            "servo": deque(),
            "general": deque(),
        }

    def pairing_origin_candidate(self, origin: str) -> bool:
        try:
            normalized = _secure_browser_origin(origin)
        except Fr5BridgeError:
            return False
        with self._pairing_lock:
            return self.paired_web_origin in {None, normalized}

    def claim_web_origin(self, origin: str, token: str) -> str:
        normalized = _secure_browser_origin(origin)
        if not hmac.compare_digest(token, self.session_token):
            raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid FR5 pairing code")
        with self._pairing_lock:
            if self.paired_web_origin is None:
                self.paired_web_origin = normalized
            elif self.paired_web_origin != normalized:
                raise _HTTPInputError(
                    HTTPStatus.FORBIDDEN,
                    "FR5 Connector is already paired with another browser site",
                )
        return normalized

    def allow_mutation(self, path: str) -> bool:
        key = "servo" if path == "/v1/servo/target" else "general"
        limit = 300 if key == "servo" else 30
        now = time.monotonic()
        with self._rate_lock:
            events = self._mutation_events[key]
            while events and now - events[0] >= 1.0:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
        return True


class Fr5LiveHandler(BaseHTTPRequestHandler):
    server: _BridgeHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "FR5Live/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _request_origin(self) -> str | None:
        origins = self.headers.get_all("Origin", failobj=[])
        return origins[0] if len(origins) == 1 else None

    def _cors_origin(self) -> str | None:
        origin = self._request_origin()
        if origin is None or origin == self.server.expected_origin:
            return None
        return origin if self.server.pairing_origin_candidate(origin) else None

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        cors_origin = self._cors_origin()
        self.send_header(
            "Cross-Origin-Resource-Policy", "cross-origin" if cors_origin else "same-origin"
        )
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Vary", "Origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _json(self, status: int, value: Mapping[str, object]) -> None:
        body = json.dumps(
            value, separators=(",", ":"), sort_keys=True, allow_nan=False
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header(
            "Set-Cookie",
            f"fr5_session={self.server.session_token}; Path=/v1; HttpOnly; SameSite=Strict",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; img-src data: blob:; media-src blob:; "
            "worker-src blob:; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'",
        )
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"status": "error", "error": message})

    def _enforce_request_context(
        self, *, api_request: bool, mutation: bool = False
    ) -> None:
        if self.client_address[0] != "127.0.0.1":
            raise _HTTPInputError(HTTPStatus.FORBIDDEN, "loopback client required")
        if self.headers.get_all("Host", failobj=[]) != [self.server.expected_host]:
            raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid Host header")
        fetch_values = self.headers.get_all("Sec-Fetch-Site", failobj=[])
        fetch_site = fetch_values[0] if len(fetch_values) == 1 else None
        origins = self.headers.get_all("Origin", failobj=[])
        if len(origins) > 1:
            raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid Origin header")
        origin = origins[0] if origins else None
        local_request = origin in {None, self.server.expected_origin}
        if api_request and local_request and fetch_site != "same-origin":
            raise _HTTPInputError(HTTPStatus.FORBIDDEN, "same-origin browser request required")
        if api_request and local_request:
            cookie_headers = self.headers.get_all("Cookie", failobj=[])
            if len(cookie_headers) != 1:
                raise _HTTPInputError(
                    HTTPStatus.FORBIDDEN, "valid helper session required"
                )
            cookies = SimpleCookie()
            try:
                cookies.load(cookie_headers[0])
            except Exception as exc:
                raise _HTTPInputError(
                    HTTPStatus.FORBIDDEN, "valid helper session required"
                ) from exc
            supplied = cookies.get("fr5_session")
            if supplied is None or not hmac.compare_digest(
                supplied.value, self.server.session_token
            ):
                raise _HTTPInputError(
                    HTTPStatus.FORBIDDEN, "valid helper session required"
                )
        elif api_request:
            if fetch_site not in {"cross-site", "same-site"} or origin is None:
                raise _HTTPInputError(HTTPStatus.FORBIDDEN, "deployed browser Origin required")
            tokens = self.headers.get_all("X-FR5-Bridge-Token", failobj=[])
            if len(tokens) != 1:
                raise _HTTPInputError(HTTPStatus.FORBIDDEN, "FR5 pairing code required")
            self.server.claim_web_origin(origin, tokens[0])
        if not api_request and fetch_site not in {"same-origin", "none"}:
            raise _HTTPInputError(
                HTTPStatus.FORBIDDEN, "cross-site frontend navigation is not permitted"
            )
        if not api_request and origin is not None and origin != self.server.expected_origin:
            raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid Origin header")
        if mutation and origin is None:
            raise _HTTPInputError(
                HTTPStatus.FORBIDDEN,
                "state-changing requests require a browser Origin",
            )

    def _read_json(self, *, maximum_bytes: int = 64 * 1024) -> Mapping[str, object]:
        if self.headers.get("Transfer-Encoding") is not None:
            raise _HTTPInputError(HTTPStatus.BAD_REQUEST, "Transfer-Encoding is not supported")
        if self.headers.get("Content-Encoding") is not None:
            raise _HTTPInputError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Encoding is not supported"
            )
        content_types = self.headers.get_all("Content-Type", failobj=[])
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            raise _HTTPInputError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json"
            )
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1 or re.fullmatch(r"\d+", lengths[0], re.ASCII) is None:
            raise _HTTPInputError(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
        length = int(lengths[0])
        if length > maximum_bytes:
            raise _HTTPInputError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise _HTTPInputError(HTTPStatus.BAD_REQUEST, "incomplete request body")

        def without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise _HTTPInputError(
                        HTTPStatus.BAD_REQUEST, f"duplicate JSON field: {key}"
                    )
                result[key] = value
            return result

        try:
            value = json.loads(
                raw.decode("utf-8") if raw else "{}",
                object_pairs_hook=without_duplicates,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite number: {item}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise _HTTPInputError(
                HTTPStatus.BAD_REQUEST, "request body is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(value, Mapping):
            raise _HTTPInputError(
                HTTPStatus.BAD_REQUEST, "request body must be a JSON object"
            )
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            if self.client_address[0] != "127.0.0.1":
                raise _HTTPInputError(HTTPStatus.FORBIDDEN, "loopback client required")
            if self.headers.get_all("Host", failobj=[]) != [self.server.expected_host]:
                raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid Host header")
            origin = self._request_origin()
            if origin is None or not self.server.pairing_origin_candidate(origin):
                raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid deployed browser Origin")
            method = self.headers.get("Access-Control-Request-Method")
            if method not in {"GET", "POST"}:
                raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid preflight method")
            requested_headers = {
                item.strip().lower()
                for item in self.headers.get("Access-Control-Request-Headers", "").split(",")
                if item.strip()
            }
            if not requested_headers <= {"content-type", "x-fr5-bridge-token"}:
                raise _HTTPInputError(HTTPStatus.FORBIDDEN, "invalid preflight headers")
            self.send_response(HTTPStatus.NO_CONTENT)
            self._security_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, X-FR5-Bridge-Token"
            )
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except _HTTPInputError as exc:
            self._error(exc.status, str(exc))

    def do_GET(self) -> None:  # noqa: N802
        try:
            raw_path, separator, query = self.path.partition("?")
            if "#" in self.path:
                raise _HTTPInputError(HTTPStatus.BAD_REQUEST, "fragments are not accepted")
            if raw_path in {"/", "/index.html"}:
                if separator and query != "robot=fr5":
                    raise _HTTPInputError(HTTPStatus.BAD_REQUEST, "only FR5 mode is served")
                self._enforce_request_context(api_request=False)
                self._html(self.server.frontend_bytes)
                return
            if separator:
                raise _HTTPInputError(HTTPStatus.BAD_REQUEST, "API query strings are not accepted")
            self._enforce_request_context(api_request=True)
            if raw_path == "/v1/health":
                self._json(HTTPStatus.OK, self.server.bridge.health())
            elif raw_path == "/v1/state":
                self._json(HTTPStatus.OK, self.server.bridge.snapshot())
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except _HTTPInputError as exc:
            self._error(exc.status, str(exc))
        except Fr5BridgeError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal bridge error")

    def do_POST(self) -> None:  # noqa: N802
        try:
            if "?" in self.path or "#" in self.path:
                raise _HTTPInputError(HTTPStatus.BAD_REQUEST, "query strings are not accepted")
            self._enforce_request_context(api_request=True, mutation=True)
            if not self.server.allow_mutation(self.path):
                raise _HTTPInputError(
                    HTTPStatus.TOO_MANY_REQUESTS, "too many state-changing requests"
                )
            maximum = MAX_LUA_BYTES + 256 * 1024 if self.path == "/v1/play" else 64 * 1024
            payload = self._read_json(maximum_bytes=maximum)
            routes = {
                "/v1/connect": self.server.bridge.connect,
                "/v1/disconnect": self.server.bridge.disconnect,
                "/v1/servo/start": self.server.bridge.begin_servo,
                "/v1/servo/target": self.server.bridge.set_servo_target,
                "/v1/servo/stop": self.server.bridge.end_servo,
                "/v1/play": self.server.bridge.start_playback,
                "/v1/pause": self.server.bridge.pause,
                "/v1/resume": self.server.bridge.resume,
                "/v1/stop": self.server.bridge.stop,
            }
            action = routes.get(self.path)
            if action is None:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
                return
            value = action(payload)
            status = HTTPStatus.CREATED if self.path == "/v1/play" else HTTPStatus.OK
            if self.path == "/v1/stop":
                status = HTTPStatus.ACCEPTED
            self._json(status, value)
        except _HTTPInputError as exc:
            self._error(exc.status, str(exc))
        except Fr5BridgeConflict as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except Fr5BridgeError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal bridge error")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Guarded physical FAIRINO FR5 companion for the choreography app"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--robot-ip", default=DEFAULT_ROBOT_IP)
    parser.add_argument("--allow-motion", action="store_true")
    parser.add_argument("--jog-speed-deg-s", type=float, default=DEFAULT_JOG_SPEED_DEG_S)
    parser.add_argument(
        "--playback-speed-percent",
        type=int,
        default=DEFAULT_PLAYBACK_SPEED_PERCENT,
    )
    parser.add_argument("--allow-full-speed", action="store_true")
    parser.add_argument("--program-prefix", default="/fruser/")
    parser.add_argument("--results-dir", default="runs/fr5-live")
    parser.add_argument(
        "--frontend",
        help="trusted bundled choreography HTML served from the helper origin",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("FR5 helper may bind only to loopback")
    if args.playback_speed_percent > 25 and not args.allow_full_speed:
        raise SystemExit(
            "playback above 25% requires the explicit --allow-full-speed startup flag"
        )
    results_path = _absolute_path(args.results_dir)
    repository_root = Path(__file__).resolve().parents[2]
    development_frontend = repository_root / "choreography-app" / "index.html"
    deployed_frontend = repository_root / "index.html"
    if args.frontend:
        frontend_path = Path(args.frontend).resolve()
    elif development_frontend.is_file():
        frontend_path = development_frontend
    elif deployed_frontend.is_file():
        frontend_path = deployed_frontend
    else:
        # Standalone macOS connector builds do not need to bundle the 16 MB
        # editor because the operator uses the deployed HTTPS app.  Keep a
        # fixed local landing page so the loopback origin still serves only
        # reviewed content.
        frontend_path = results_path / "connector.html"
        frontend_path.parent.mkdir(parents=True, exist_ok=True)
        frontend_path.write_text(_CONNECTOR_LANDING_HTML, encoding="utf-8")
    if not frontend_path.is_file():
        raise SystemExit("trusted choreography frontend is unavailable; pass --frontend")
    bridge = Fr5LiveBridge(
        robot_ip=args.robot_ip,
        results_directory=results_path,
        allow_motion=args.allow_motion,
        jog_speed_deg_s=args.jog_speed_deg_s,
        playback_speed_percent=args.playback_speed_percent,
        program_prefix=args.program_prefix,
    )
    server = _BridgeHTTPServer(
        ("127.0.0.1", args.port), bridge, frontend_path=frontend_path
    )
    status_path = results_path / "server.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "running",
                "pid": os.getpid(),
                "host": "127.0.0.1",
                "port": args.port,
                "robot_ip": bridge.robot_ip,
                "motion_enabled": bridge.allow_motion,
                "started_at": _utc_now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"FR5 helper ready at http://127.0.0.1:{args.port}/?robot=fr5 "
        f"({'motion enabled' if args.allow_motion else 'read-only'})",
        flush=True,
    )
    print(f"FR5 pairing code: {server.session_token}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
        server.server_close()
        status_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "stopped",
                    "pid": os.getpid(),
                    "host": "127.0.0.1",
                    "port": args.port,
                    "stopped_at": _utc_now(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
