import copy
from dataclasses import FrozenInstanceError

import pytest

from macro_studio.mouse_motion import (
    BUILTIN_PROFILES,
    DEFAULT_PROFILE,
    DEFAULT_PROFILE_ID,
    MouseMotionProfile,
    MouseTrajectory,
    PreviewContext,
    TimedPoint,
    sample_count,
    undersampling_warnings,
)


def test_builtin_presets_match_contract_and_cannot_be_replaced():
    expected = {
        "builtin.direct": ("Direct", 0.00, 40.0, 0.0, 0.0, 0.0, 120.0),
        "builtin.precision": ("Precision", 0.04, 50.0, 0.5, 5.0, 0.3, 160.0),
        "builtin.smooth": ("Smooth", 0.08, 90.0, 0.7, 3.0, 1.2, 220.0),
        "builtin.natural": ("Natural", 0.15, 40.0, 2.0, 8.0, 1.0, 120.0),
        "builtin.tremor": ("Tremor", 0.20, 35.0, 4.0, 14.0, 1.0, 100.0),
    }
    assert DEFAULT_PROFILE_ID == "builtin.natural"
    assert DEFAULT_PROFILE is BUILTIN_PROFILES[DEFAULT_PROFILE_ID]
    assert set(BUILTIN_PROFILES) == set(expected)
    for profile_id, values in expected.items():
        profile = BUILTIN_PROFILES[profile_id]
        assert (profile.name, profile.speed_sigma, profile.speed_correlation_ms,
                profile.tremor_amplitude_px, profile.tremor_frequency_hz,
                profile.drift_amplitude_px, profile.drift_correlation_ms) == values
    with pytest.raises(TypeError):
        BUILTIN_PROFILES["builtin.direct"] = DEFAULT_PROFILE
    with pytest.raises(FrozenInstanceError):
        DEFAULT_PROFILE.name = "changed"


def test_nested_json_round_trip_preserves_identity_revision_and_parameters():
    source = {
        "motion_profile_schema_version": 1.0,
        "id": "user.17c6c5b6-7f4f-4e12-943a-2f3d6a387d95",
        "name": "Custom Subtle Tremor",
        "origin": "user",
        "revision": 3.0,
        "parameters": {
            "duration_ms": 350.0,
            "base_curve": "smoothstep",
            "sampling_hz": 200.0,
            "speed": {"sigma": 0.15, "correlation_ms": 40.0},
            "lateral": {
                "tremor_amplitude_px": 2.0,
                "tremor_frequency_hz": 8.0,
                "drift_amplitude_px": 1.0,
                "drift_correlation_ms": 120.0,
            },
            "endpoint_envelope": "sin_squared",
            "seed_policy": "random_per_execution",
        },
    }
    profile = MouseMotionProfile.from_dict(source)
    assert profile.id == source["id"]
    assert profile.revision == 3
    assert profile.sampling_hz == 200
    assert profile.to_dict() == {**source, "motion_profile_schema_version": 1, "revision": 3.0,
                                 "parameters": {**source["parameters"], "sampling_hz": 200}}
    assert MouseMotionProfile.from_dict(profile.to_dict()) == profile


def test_profile_json_rejects_extra_keys_invalid_identity_and_unsupported_versions():
    source = DEFAULT_PROFILE.to_dict()
    output_leak = copy.deepcopy(source)
    output_leak["parameters"]["com_port"] = "COM5"
    with pytest.raises(ValueError, match="unknown com_port"):
        MouseMotionProfile.from_dict(output_leak)
    with pytest.raises(ValueError, match="unsupported"):
        MouseMotionProfile.from_dict({**source, "motion_profile_schema_version": 2})
    with pytest.raises(ValueError, match="canonical lowercase uuid"):
        MouseMotionProfile.from_dict({**source, "id": "user.17C6C5B6-7F4F-4E12-943A-2F3D6A387D95", "origin": "user"})
    with pytest.raises(ValueError, match="unknown builtin"):
        MouseMotionProfile.from_dict({**source, "id": "builtin.unknown"})


@pytest.mark.parametrize(("field", "value"), [
    ("duration_ms", float("nan")),
    ("speed_sigma", 1.51),
    ("speed_correlation_ms", 0.0),
    ("tremor_amplitude_px", 200.1),
    ("tremor_frequency_hz", 100.1),
    ("drift_amplitude_px", -0.1),
    ("drift_correlation_ms", 5001.0),
    ("base_curve", "bezier"),
    ("endpoint_envelope", "linear"),
    ("seed_policy", "preview"),
])
def test_profile_rejects_values_outside_the_version_one_contract(field, value):
    source = DEFAULT_PROFILE.to_dict()
    if field in {"base_curve", "endpoint_envelope", "seed_policy"}:
        source["parameters"][field] = value
    elif field == "duration_ms":
        source["parameters"][field] = value
    elif field in {"speed_sigma", "speed_correlation_ms"}:
        source["parameters"]["speed"][{"speed_sigma": "sigma", "speed_correlation_ms": "correlation_ms"}[field]] = value
    else:
        source["parameters"]["lateral"][field] = value
    with pytest.raises(ValueError):
        MouseMotionProfile.from_dict(source)


def test_nyquist_is_an_error_and_high_but_valid_tremor_is_a_warning():
    assert undersampling_warnings(40.1, 200)
    assert not undersampling_warnings(40.0, 200)
    with pytest.raises(ValueError, match="Nyquist"):
        undersampling_warnings(100.0, 200)
    profile = DEFAULT_PROFILE.to_dict()
    profile["parameters"]["lateral"]["tremor_frequency_hz"] = 100.0
    with pytest.raises(ValueError, match="Nyquist"):
        MouseMotionProfile.from_dict(profile)


def test_c5_4_sample_count_includes_both_endpoints_and_enforces_limits():
    assert sample_count(350.0, 200) == 71
    assert sample_count(1.0, 10) == 2
    with pytest.raises(ValueError, match="maximum"):
        sample_count(60_000.0, 2000)
    with pytest.raises(ValueError, match="duplicate"):
        sample_count(0.0000005, 1000)
    with pytest.raises(ValueError, match="maximum"):
        sample_count(1e308, 1000)


def test_immutable_trajectory_preview_and_timed_point_contracts():
    points = (TimedPoint(0, 1.0, 2.0), TimedPoint(1_000_000, 3.0, 4.0))
    trajectory = MouseTrajectory(points, (1.0, 2.0), (3.0, 4.0), 1_000_000,
                                 DEFAULT_PROFILE_ID, 7, 1)
    assert trajectory.points[0].time_offset_ns == 0
    assert trajectory.points[-1].time_offset_ns == trajectory.duration_ns
    assert PreviewContext().start_xy == (100.0, 100.0)
    assert PreviewContext().target_xy == (800.0, 500.0)
    with pytest.raises(ValueError, match="strictly increasing"):
        MouseTrajectory((points[0], TimedPoint(0, 2.0, 3.0), points[-1]),
                        (1.0, 2.0), (3.0, 4.0), 1_000_000, DEFAULT_PROFILE_ID, 7, 1)




def test_json_boundary_normalizes_malformed_objects_and_overflow_to_value_error():
    with pytest.raises(ValueError, match="profile must be an object"):
        MouseMotionProfile.from_dict([])
    with pytest.raises(ValueError, match="keys must be strings"):
        MouseMotionProfile.from_dict({1: "bad"})
    source = DEFAULT_PROFILE.to_dict()
    source["parameters"]["duration_ms"] = 10**1000
    with pytest.raises(ValueError, match="finite number"):
        MouseMotionProfile.from_dict(source)
