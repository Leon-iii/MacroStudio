import math
from itertools import pairwise

import pytest

from macro_studio.mouse_motion import BUILTIN_PROFILES, MotionGenerator, TimedPoint


@pytest.mark.parametrize("profile", BUILTIN_PROFILES.values(), ids=BUILTIN_PROFILES.keys())
def test_same_seed_reproduces_points_with_exact_endpoints_and_forward_progress(profile):
    start, target = (-3.5, 82.125), (123.25, -15.0)
    first = MotionGenerator.generate(start, target, profile, seed=23)
    assert MotionGenerator.generate(start, target, profile, seed=23) == first
    assert first.points[0] == TimedPoint(0, *start)
    assert first.points[-1] == TimedPoint(350_000_000, *target)
    assert len(first.points) == 71
    assert all(a.time_offset_ns < b.time_offset_ns for a, b in pairwise(first.points))
    dx, dy = target[0] - start[0], target[1] - start[1]
    progress = [(point.x - start[0]) * dx + (point.y - start[1]) * dy for point in first.points]
    assert all(a < b for a, b in pairwise(progress))
    if profile.id != "builtin.direct":
        assert MotionGenerator.generate(start, target, profile, seed=24).points != first.points


def test_direct_profile_follows_smoothstep_independently_of_seed():
    profile = BUILTIN_PROFILES["builtin.direct"]
    first = MotionGenerator.generate((0.0, 5.0), (64.0, 5.0), profile, 1, duration_override_ms=20)
    assert first.points == MotionGenerator.generate((0.0, 5.0), (64.0, 5.0), profile, 2, 20).points
    assert [(p.time_offset_ns, p.x, p.y) for p in first.points] == [
        (0, 0.0, 5.0), (5_000_000, 10.0, 5.0), (10_000_000, 32.0, 5.0),
        (15_000_000, 54.0, 5.0), (20_000_000, 64.0, 5.0),
    ]


def test_zero_distance_returns_one_immediate_point():
    coordinate = (-48.5, 102.25)
    motion = MotionGenerator.generate(coordinate, coordinate, BUILTIN_PROFILES["builtin.tremor"], seed=5)
    assert motion.duration_ns == 0
    assert motion.points == (TimedPoint(0, *coordinate),)


def test_short_motion_caps_lateral_offset_and_keeps_both_samples():
    profile = BUILTIN_PROFILES["builtin.tremor"]
    for duration_ms in (1, 97.25):
        motion = MotionGenerator.generate((0, 0), (0.2, 0), profile, 36, duration_ms)
        assert len(motion.points) == max(1, round(duration_ms * profile.sampling_hz / 1000)) + 1
        assert motion.points[-1] == TimedPoint(round(duration_ms * 1_000_000), 0.2, 0)
        assert all(math.isfinite(p.x) and abs(p.y) <= 0.02 + 1e-15 for p in motion.points)


@pytest.mark.parametrize("duration", [0, 60_001, math.nan])
def test_invalid_duration_override_is_rejected(duration):
    with pytest.raises(ValueError, match="duration_ms"):
        MotionGenerator.generate((0, 0), (1, 0), BUILTIN_PROFILES["builtin.natural"], 7, duration)
