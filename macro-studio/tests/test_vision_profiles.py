import pytest

from macro_studio.vision_profiles import BUILTIN_PROFILES, resolve_profile, validate_profiles


def test_profile_precedence_and_roi():
    project = {
        "vision_default_profile": "builtin.strict",
        "vision_profiles": {"user.quick": {"name": "Quick", "scan_interval_ms": 50}},
        "image_assets": {"button": {"recognition_profile_id": "builtin.flexible"}},
    }
    assert resolve_profile(project)["enter_threshold"] == 0.95
    inherited = resolve_profile(project, asset_id="button")
    assert inherited["enter_threshold"] == 0.82
    roi = {"left": 40, "top": 30, "width": 200, "height": 100}
    result = resolve_profile(
        project, "user.quick", "button", {"enter_threshold": 0.87, "roi": {"kind": "source", **roi}},
        application_profile="builtin.balanced",
    )
    assert result["enter_threshold"] == 0.87
    assert result["exit_threshold"] == 0.75
    assert result["scan_interval_ms"] == 50
    assert result["roi"] == roi
    assert BUILTIN_PROFILES["builtin.flexible"]["enter_threshold"] == 0.82


@pytest.mark.parametrize("profile", [
    {"name": ""},
    {"name": "Invalid", "enter_threshold": 0.3, "exit_threshold": 0.8},
    {"name": "Invalid", "max_age_ms": True},
    {"name": "Invalid", "roi": {"left": 0, "top": 0, "width": 0, "height": 50}},
])
def test_invalid_user_profile_is_rejected(profile):
    with pytest.raises(ValueError):
        validate_profiles({"user.invalid": profile})


def test_missing_profile_has_an_explicit_error():
    with pytest.raises(ValueError, match="user.missing"):
        resolve_profile({}, "user.missing")
