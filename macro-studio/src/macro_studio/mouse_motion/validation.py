from __future__ import annotations

import math

MAX_TRAJECTORY_SAMPLES = 100_000


def validate_profile_values(
    *, duration_ms: float, sampling_hz: int, base_curve: str,
    speed_sigma: float, speed_correlation_ms: float,
    tremor_amplitude_px: float, tremor_frequency_hz: float,
    drift_amplitude_px: float, drift_correlation_ms: float,
    endpoint_envelope: str, seed_policy: str,
) -> None:
    bounded_number(duration_ms, "duration_ms", 1.0, 60_000.0)
    if isinstance(sampling_hz, bool) or not isinstance(sampling_hz, int) or not 10 <= sampling_hz <= 1000:
        raise ValueError("sampling_hz must be an integer between 10 and 1000")
    bounded_number(speed_sigma, "speed_sigma", 0.0, 1.5)
    bounded_number(speed_correlation_ms, "speed_correlation_ms", 1.0, 5000.0)
    bounded_number(tremor_amplitude_px, "tremor_amplitude_px", 0.0, 200.0)
    bounded_number(tremor_frequency_hz, "tremor_frequency_hz", 0.0, 100.0)
    bounded_number(drift_amplitude_px, "drift_amplitude_px", 0.0, 200.0)
    bounded_number(drift_correlation_ms, "drift_correlation_ms", 1.0, 5000.0)
    if base_curve != "smoothstep":
        raise ValueError("base_curve must be 'smoothstep'")
    if endpoint_envelope != "sin_squared":
        raise ValueError("endpoint_envelope must be 'sin_squared'")
    if seed_policy not in {"fixed", "random_per_execution"}:
        raise ValueError("seed_policy must be 'fixed' or 'random_per_execution'")
    undersampling_warnings(tremor_frequency_hz, sampling_hz)
    sample_count(duration_ms, sampling_hz)


def sample_count(duration_ms: float, sampling_hz: int) -> int:
    """Return the C5.4 M+1 sample count, rejecting unsafe or duplicate timestamps."""
    duration = finite_number(duration_ms, "duration_ms")
    if duration <= 0:
        raise ValueError("duration_ms must be positive")
    if isinstance(sampling_hz, bool) or not isinstance(sampling_hz, int) or sampling_hz <= 0:
        raise ValueError("sampling_hz must be a positive integer")
    try:
        intervals = max(1, round(duration / 1000.0 * sampling_hz))
    except (OverflowError, ValueError) as exc:
        raise ValueError("requested sample count is not representable") from exc
    count = intervals + 1
    if count > MAX_TRAJECTORY_SAMPLES:
        raise ValueError(f"trajectory exceeds maximum sample count of {MAX_TRAJECTORY_SAMPLES}")
    try:
        duration_ns = round(duration * 1_000_000)
    except (OverflowError, ValueError) as exc:
        raise ValueError("duration nanoseconds are not representable") from exc
    if duration_ns < intervals:
        raise ValueError("trajectory sample offsets would duplicate integer nanosecond timestamps")
    return count


def undersampling_warnings(tremor_frequency_hz: float, sampling_hz: int) -> tuple[str, ...]:
    frequency = finite_number(tremor_frequency_hz, "tremor_frequency_hz")
    if isinstance(sampling_hz, bool) or not isinstance(sampling_hz, int) or sampling_hz <= 0:
        raise ValueError("sampling_hz must be a positive integer")
    if frequency < 0:
        raise ValueError("tremor_frequency_hz must be non-negative")
    if frequency >= sampling_hz / 2:
        raise ValueError(
            f"Nyquist violation: tremor_frequency_hz ({frequency:g}) must be below "
            f"sampling_hz / 2 ({sampling_hz / 2:g})"
        )
    if frequency > sampling_hz / 5:
        return ("Tremor frequency exceeds one fifth of sampling rate; visual aliasing may occur.",)
    return ()


def finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def bounded_number(value: object, label: str, minimum: float, maximum: float) -> None:
    number = finite_number(value, label)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")


def finite_pair(value: object, label: str) -> None:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{label} must be a pair")
    finite_number(value[0], f"{label}[0]")
    finite_number(value[1], f"{label}[1]")


def json_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError(f"{label} must be an integer")
    try:
        return int(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
