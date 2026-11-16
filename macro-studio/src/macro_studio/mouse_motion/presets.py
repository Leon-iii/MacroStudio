from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .models import MouseMotionProfile

DEFAULT_PROFILE_ID = "builtin.natural"


def _profile(
    profile_id: str, name: str, sigma: float, speed_tau: float,
    tremor_amplitude: float, tremor_frequency: float,
    drift_amplitude: float, drift_tau: float,
) -> MouseMotionProfile:
    return MouseMotionProfile(
        id=profile_id,
        name=name,
        origin="builtin",
        revision=1,
        duration_ms=350.0,
        sampling_hz=200,
        base_curve="smoothstep",
        speed_sigma=sigma,
        speed_correlation_ms=speed_tau,
        tremor_amplitude_px=tremor_amplitude,
        tremor_frequency_hz=tremor_frequency,
        drift_amplitude_px=drift_amplitude,
        drift_correlation_ms=drift_tau,
        endpoint_envelope="sin_squared",
        seed_policy="random_per_execution",
    )


BUILTIN_PROFILES: Mapping[str, MouseMotionProfile] = MappingProxyType({
    profile.id: profile
    for profile in (
        _profile("builtin.direct", "Direct", 0.00, 40.0, 0.0, 0.0, 0.0, 120.0),
        _profile("builtin.precision", "Precision", 0.04, 50.0, 0.5, 5.0, 0.3, 160.0),
        _profile("builtin.smooth", "Smooth", 0.08, 90.0, 0.7, 3.0, 1.2, 220.0),
        _profile("builtin.natural", "Natural", 0.15, 40.0, 2.0, 8.0, 1.0, 120.0),
        _profile("builtin.tremor", "Tremor", 0.20, 35.0, 4.0, 14.0, 1.0, 100.0),
    )
})
DEFAULT_PROFILE = BUILTIN_PROFILES[DEFAULT_PROFILE_ID]
