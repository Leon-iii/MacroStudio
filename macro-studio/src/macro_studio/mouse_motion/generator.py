import math
from collections.abc import Iterator
from itertools import accumulate
from random import Random

from .models import MouseMotionProfile, MouseTrajectory, TimedPoint
from .validation import bounded_number, finite_pair, sample_count

GENERATOR_VERSION = 1


def _correlated_noise(rng: Random, step_ms: float, correlation_ms: float) -> Iterator[float]:
    decay = -step_ms / correlation_ms
    alpha = math.exp(decay)
    innovation = math.sqrt(-math.expm1(2 * decay))
    state = 0.0
    while True:
        state = alpha * state + innovation * rng.gauss(0, 1)
        yield state


class MotionGenerator:
    @staticmethod
    def generate(
        start_xy: tuple[float, float],
        target_xy: tuple[float, float],
        profile: MouseMotionProfile,
        seed: int,
        duration_override_ms: float | None = None,
    ) -> MouseTrajectory:
        finite_pair(start_xy, "start_xy")
        finite_pair(target_xy, "target_xy")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        duration_ms = profile.duration_ms if duration_override_ms is None else duration_override_ms
        bounded_number(duration_ms, "duration_ms", 1.0, 60_000.0)
        start = TimedPoint(0, *start_xy)
        if start_xy == target_xy:
            return MouseTrajectory((start,), start_xy, target_xy, 0, profile.id, seed, GENERATOR_VERSION)

        dx = float(target_xy[0]) - float(start_xy[0])
        dy = float(target_xy[1]) - float(start_xy[1])
        distance = math.hypot(dx, dy)
        if not (0 < distance < math.inf):
            raise ValueError("movement distance is not representable")
        normal_x, normal_y = -dy / distance, dx / distance
        duration_ns = round(duration_ms * 1_000_000)
        intervals = sample_count(duration_ms, profile.sampling_hz) - 1
        step_ms = duration_ms / intervals

        rng = Random(seed)
        speed = _correlated_noise(Random(rng.getrandbits(128)), step_ms, profile.speed_correlation_ms)
        drift = _correlated_noise(Random(rng.getrandbits(128)), step_ms, profile.drift_correlation_ms)
        phase = rng.random() * math.tau
        if profile.speed_sigma:
            weights = (
                max(1e-12, 6 * u * (1 - u) * math.exp(profile.speed_sigma * next(speed)))
                for u in ((index + 0.5) / intervals for index in range(intervals))
            )
            totals = list(accumulate(weights))
            progress = (total / totals[-1] for total in totals[:-1])
        else:
            progress = (u * u * (3 - 2 * u) for u in (index / intervals for index in range(1, intervals)))

        cap = 0.1 * distance
        tremor_amplitude = min(profile.tremor_amplitude_px, cap)
        drift_amplitude = min(profile.drift_amplitude_px, cap)
        angular_step = math.tau * profile.tremor_frequency_hz * step_ms / 1000
        points = [start]
        for index, s in enumerate(progress, start=1):
            lateral = math.sin(math.pi * s) ** 2 * (
                tremor_amplitude * math.sin(index * angular_step + phase) + drift_amplitude * next(drift)
            )
            lateral = max(-cap, min(cap, lateral))
            points.append(TimedPoint(
                duration_ns * index // intervals,
                start_xy[0] + s * dx + lateral * normal_x,
                start_xy[1] + s * dy + lateral * normal_y,
            ))
        points.append(TimedPoint(duration_ns, *target_xy))
        return MouseTrajectory(tuple(points), start_xy, target_xy, duration_ns, profile.id, seed, GENERATOR_VERSION)
