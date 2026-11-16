"""Recognition profiles and their precedence in project files."""

DEFAULT_PROFILE = "builtin.balanced"


def _preset(name, enter, exit_, appear, disappear):
    return {
        "name": name,
        "detector": "template",
        "method": "TM_CCOEFF_NORMED",
        "enter_threshold": enter,
        "exit_threshold": exit_,
        "appearance_frames": appear,
        "disappearance_frames": disappear,
        "cooldown_ms": 500,
        "max_age_ms": 250,
        "scan_interval_ms": 33,
    }


BUILTIN_PROFILES = {
    "builtin.strict": _preset("Strict", 0.95, 0.88, 3, 3),
    DEFAULT_PROFILE: _preset("Balanced", 0.90, 0.82, 2, 2),
    "builtin.flexible": _preset("Flexible", 0.82, 0.75, 2, 3),
}

_PROFILE_FIELDS = frozenset(BUILTIN_PROFILES[DEFAULT_PROFILE]) | {"roi"}


def validate_roi(roi):
    if roi is None:
        return
    if (not isinstance(roi, dict)
            or set(roi) - {"kind", "left", "top", "width", "height"}
            or roi.get("kind", "source") != "source"
            or any(type(roi.get(key)) is not int for key in ("left", "top", "width", "height"))
            or not (0 <= roi["left"] <= 32767 and 0 <= roi["top"] <= 32767)
            or not (1 <= roi["width"] <= 32768 and 1 <= roi["height"] <= 32768)):
        raise ValueError("탐색 영역은 소스 안에서 양의 너비와 높이를 가져야 합니다.")


def validate_profiles(records):
    if not isinstance(records, dict):
        raise ValueError("인식 프로파일 목록이 올바르지 않습니다.")
    for profile_id, profile in records.items():
        if not isinstance(profile_id, str) or not profile_id.startswith("user.") or not profile_id[5:]:
            raise ValueError(f"사용자 인식 프로파일 ID가 올바르지 않습니다. {profile_id}")
        if not isinstance(profile, dict) or set(profile) - _PROFILE_FIELDS:
            raise ValueError(f"인식 프로파일 필드가 올바르지 않습니다. {profile_id}")
        name = profile.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("인식 프로파일 이름은 1~100자로 입력하세요.")
        _validate({**BUILTIN_PROFILES[DEFAULT_PROFILE], **profile})


def _validate(profile):
    if profile["detector"] not in ("template", "hsv"):
        raise ValueError("지원하지 않는 인식 방식입니다.")
    if profile["method"] != "TM_CCOEFF_NORMED":
        raise ValueError("지원하지 않는 템플릿 매칭 방식입니다.")
    enter, exit_ = profile["enter_threshold"], profile["exit_threshold"]
    if (any(type(value) not in (int, float) for value in (enter, exit_))
            or not -1 <= exit_ <= enter <= 1):
        raise ValueError("이탈 임계값은 진입 임계값 이하여야 하며 두 값 모두 -1~1이어야 합니다.")
    limits = {
        "appearance_frames": (1, 300),
        "disappearance_frames": (1, 300),
        "cooldown_ms": (0, 60_000),
        "max_age_ms": (1, 60_000),
        "scan_interval_ms": (16, 60_000),
    }
    for key, (minimum, maximum) in limits.items():
        value = profile[key]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{key}에는 {minimum}~{maximum} 사이의 정수를 입력하세요.")
    validate_roi(profile.get("roi"))


def resolve_profile(project, profile_ref="inherit", asset_id=None, overrides=None,
                    *, application_profile=DEFAULT_PROFILE):
    """Return a copy of the effective settings for a detector."""
    users = project.get("vision_profiles", {})
    validate_profiles(users)
    asset = project.get("image_assets", {}).get(asset_id, {})
    profiles = {**BUILTIN_PROFILES, **users}
    resolved = dict(BUILTIN_PROFILES[DEFAULT_PROFILE])
    for ref in (application_profile, project.get("vision_default_profile"),
                asset.get("recognition_profile_id"), profile_ref):
        if ref is None or ref == "inherit":
            continue
        if not isinstance(ref, str) or ref not in profiles:
            raise ValueError(f"인식 프로파일을 찾을 수 없습니다. {ref}")
        resolved.update(profiles[ref])
    resolved.update(overrides or {})
    _validate(resolved)
    if resolved.get("roi") is not None:
        resolved["roi"] = {key: value for key, value in resolved["roi"].items() if key != "kind"}
    del resolved["name"]
    return resolved
