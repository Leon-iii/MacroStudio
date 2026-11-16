from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Mapping

from .validation import bounded_number, finite_number, finite_pair, json_integer, undersampling_warnings, validate_profile_values


@dataclass(frozen=True, slots=True)
class MouseMotionProfile:
    id: str
    name: str
    origin: str
    revision: int
    duration_ms: float
    sampling_hz: int
    base_curve: str
    speed_sigma: float
    speed_correlation_ms: float
    tremor_amplitude_px: float
    tremor_frequency_hz: float
    drift_amplitude_px: float
    drift_correlation_ms: float
    endpoint_envelope: str
    seed_policy: str = "random_per_execution"

    def __post_init__(self) -> None:
        _validate_identity(self.id, self.name, self.origin, self.revision)
        validate_profile_values(
            duration_ms=self.duration_ms,
            sampling_hz=self.sampling_hz,
            base_curve=self.base_curve,
            speed_sigma=self.speed_sigma,
            speed_correlation_ms=self.speed_correlation_ms,
            tremor_amplitude_px=self.tremor_amplitude_px,
            tremor_frequency_hz=self.tremor_frequency_hz,
            drift_amplitude_px=self.drift_amplitude_px,
            drift_correlation_ms=self.drift_correlation_ms,
            endpoint_envelope=self.endpoint_envelope,
            seed_policy=self.seed_policy,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MouseMotionProfile:
        _expect_keys(value, {
            "motion_profile_schema_version", "id", "name", "origin", "revision", "parameters"
        }, "profile")
        schema_version = json_integer(value["motion_profile_schema_version"], "motion_profile_schema_version")
        if schema_version != 1:
            raise ValueError(f"unsupported motion profile schema version: {schema_version}")
        parameters = value["parameters"]
        if not isinstance(parameters, Mapping):
            raise ValueError("parameters must be an object")
        _expect_keys(parameters, {
            "duration_ms", "base_curve", "sampling_hz", "speed", "lateral",
            "endpoint_envelope", "seed_policy"
        }, "parameters")
        speed = parameters["speed"]
        lateral = parameters["lateral"]
        if not isinstance(speed, Mapping):
            raise ValueError("parameters.speed must be an object")
        if not isinstance(lateral, Mapping):
            raise ValueError("parameters.lateral must be an object")
        _expect_keys(speed, {"sigma", "correlation_ms"}, "parameters.speed")
        _expect_keys(lateral, {
            "tremor_amplitude_px", "tremor_frequency_hz", "drift_amplitude_px", "drift_correlation_ms"
        }, "parameters.lateral")
        return cls(
            id=_string(value["id"], "id"),
            name=_string(value["name"], "name"),
            origin=_string(value["origin"], "origin"),
            revision=json_integer(value["revision"], "revision"),
            duration_ms=finite_number(parameters["duration_ms"], "parameters.duration_ms"),
            sampling_hz=json_integer(parameters["sampling_hz"], "parameters.sampling_hz"),
            base_curve=_string(parameters["base_curve"], "parameters.base_curve"),
            speed_sigma=finite_number(speed["sigma"], "parameters.speed.sigma"),
            speed_correlation_ms=finite_number(speed["correlation_ms"], "parameters.speed.correlation_ms"),
            tremor_amplitude_px=finite_number(lateral["tremor_amplitude_px"], "parameters.lateral.tremor_amplitude_px"),
            tremor_frequency_hz=finite_number(lateral["tremor_frequency_hz"], "parameters.lateral.tremor_frequency_hz"),
            drift_amplitude_px=finite_number(lateral["drift_amplitude_px"], "parameters.lateral.drift_amplitude_px"),
            drift_correlation_ms=finite_number(lateral["drift_correlation_ms"], "parameters.lateral.drift_correlation_ms"),
            endpoint_envelope=_string(parameters["endpoint_envelope"], "parameters.endpoint_envelope"),
            seed_policy=_string(parameters["seed_policy"], "parameters.seed_policy"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "motion_profile_schema_version": 1,
            "id": self.id,
            "name": self.name,
            "origin": self.origin,
            "revision": self.revision,
            "parameters": {
                "duration_ms": self.duration_ms,
                "base_curve": self.base_curve,
                "sampling_hz": self.sampling_hz,
                "speed": {
                    "sigma": self.speed_sigma,
                    "correlation_ms": self.speed_correlation_ms,
                },
                "lateral": {
                    "tremor_amplitude_px": self.tremor_amplitude_px,
                    "tremor_frequency_hz": self.tremor_frequency_hz,
                    "drift_amplitude_px": self.drift_amplitude_px,
                    "drift_correlation_ms": self.drift_correlation_ms,
                },
                "endpoint_envelope": self.endpoint_envelope,
                "seed_policy": self.seed_policy,
            },
        }

    @property
    def warnings(self) -> tuple[str, ...]:
        if self.tremor_frequency_hz > self.sampling_hz / 5:
            return ("Tremor frequency exceeds one fifth of sampling rate; visual aliasing may occur.",)
        return ()


@dataclass(frozen=True, slots=True)
class TimedPoint:
    time_offset_ns: int
    x: float
    y: float

    def __post_init__(self) -> None:
        if isinstance(self.time_offset_ns, bool) or not isinstance(self.time_offset_ns, int) or self.time_offset_ns < 0:
            raise ValueError("time_offset_ns must be a non-negative integer")
        finite_number(self.x, "x")
        finite_number(self.y, "y")


@dataclass(frozen=True, slots=True)
class MouseTrajectory:
    points: tuple[TimedPoint, ...]
    start: tuple[float, float]
    target: tuple[float, float]
    duration_ns: int
    profile_id: str
    seed: int
    generator_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.points, tuple) or not all(isinstance(p, TimedPoint) for p in self.points):
            raise ValueError("points must be a tuple of TimedPoint values")
        if isinstance(self.duration_ns, bool) or not isinstance(self.duration_ns, int) or self.duration_ns < 0:
            raise ValueError("duration_ns must be a non-negative integer")
        finite_pair(self.start, "start")
        finite_pair(self.target, "target")
        if self.start == self.target:
            if self.duration_ns != 0 or len(self.points) != 1:
                raise ValueError("zero-distance trajectory must be a single immediate point")
            if self.points[0].time_offset_ns != 0 or (self.points[0].x, self.points[0].y) != self.start:
                raise ValueError("zero-distance trajectory point must equal start at time zero")
        else:
            if len(self.points) < 2 or self.duration_ns == 0:
                raise ValueError("moving trajectory requires two endpoints and positive duration")
            if any(a.time_offset_ns >= b.time_offset_ns for a, b in pairwise(self.points)):
                raise ValueError("point offsets must be strictly increasing")
            if self.points[0].time_offset_ns != 0 or self.points[-1].time_offset_ns != self.duration_ns:
                raise ValueError("trajectory points must begin at zero and end at duration_ns")
            if (self.points[0].x, self.points[0].y) != self.start:
                raise ValueError("first point must equal start")
            if (self.points[-1].x, self.points[-1].y) != self.target:
                raise ValueError("last point must equal target")
        _string(self.profile_id, "profile_id")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if isinstance(self.generator_version, bool) or not isinstance(self.generator_version, int) or self.generator_version < 1:
            raise ValueError("generator_version must be an integer >= 1")


@dataclass(frozen=True, slots=True)
class PreviewContext:
    start_xy: tuple[float, float] = (100.0, 100.0)
    target_xy: tuple[float, float] = (800.0, 500.0)
    duration_override_ms: float | None = None
    seed: int = 7
    show_baseline: bool = True
    lateral_visual_magnification: float = 1.0
    compare_previous: bool = False

    def __post_init__(self) -> None:
        finite_pair(self.start_xy, "start_xy")
        finite_pair(self.target_xy, "target_xy")
        if self.duration_override_ms is not None:
            bounded_number(self.duration_override_ms, "duration_override_ms", 1.0, 60_000.0)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        magnification = finite_number(self.lateral_visual_magnification, "lateral_visual_magnification")
        if magnification <= 0:
            raise ValueError("lateral_visual_magnification must be positive")
        if not isinstance(self.show_baseline, bool) or not isinstance(self.compare_previous, bool):
            raise ValueError("preview flags must be booleans")


def _validate_identity(profile_id: str, name: str, origin: str, revision: int) -> None:
    _string(profile_id, "id")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-blank string")
    if not isinstance(origin, str) or origin not in {"builtin", "user"}:
        raise ValueError("origin must be 'builtin' or 'user'")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision must be an integer >= 1")
    if origin == "builtin":
        if profile_id not in {"builtin.direct", "builtin.precision", "builtin.smooth", "builtin.natural", "builtin.tremor"}:
            raise ValueError("unknown builtin profile id")
    else:
        match = re.fullmatch(r"user\.([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", profile_id)
        if not match or str(uuid.UUID(match.group(1))) != match.group(1):
            raise ValueError("user id must be user.<canonical lowercase uuid>")


def _expect_keys(value: Any, required: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    keys = set(value)
    if keys != required:
        missing = sorted(required - keys)
        extra = sorted(keys - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unknown {', '.join(extra)}")
        raise ValueError(f"invalid {label} keys: {'; '.join(details)}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value
