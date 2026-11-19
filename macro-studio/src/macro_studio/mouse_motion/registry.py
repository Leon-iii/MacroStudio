import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from .models import MouseMotionProfile
from .presets import BUILTIN_PROFILES, DEFAULT_PROFILE, DEFAULT_PROFILE_ID

DEFAULT_KEY = "default_mouse_motion_profile_id"
SNAPSHOTS_KEY = "motion_profile_snapshots"


def _user_profiles(items):
    if not isinstance(items, list):
        raise ValueError("모션 프로파일 목록 형식이 잘못되었습니다.")
    profiles = {}
    for item in items:
        profile = MouseMotionProfile.from_dict(item)
        if profile.origin != "user" or profile.id in profiles:
            raise ValueError("사용자 프로파일 ID가 중복되었거나 잘못되었습니다.")
        profiles[profile.id] = profile
    return profiles


class MotionProfileRegistry:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def local(cls):
        location = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
        base = Path(location) if location else Path.home() / ".config"
        return cls(base / "MacroStudio" / "mouse-motion.json")

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return DEFAULT_PROFILE_ID, {}
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("지원하지 않는 마우스 설정 형식입니다.")
        profiles = _user_profiles(data.get("profiles"))
        default = data.get(DEFAULT_KEY)
        if not isinstance(default, str) or default not in (BUILTIN_PROFILES | profiles):
            raise ValueError("앱 기본 모션 프로파일을 찾을 수 없습니다.")
        return default, profiles

    def _write(self, default, profiles):
        data = {"schema_version": 1, DEFAULT_KEY: default,
                "profiles": [profile.to_dict() for profile in profiles.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.path.parent, delete=False, mode="w", encoding="utf-8") as file:
                temporary = Path(file.name)
                json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def set_default(self, profile_id):
        _, users = self.load()
        if profile_id not in (BUILTIN_PROFILES | users):
            raise ValueError("모션 프로파일을 찾을 수 없습니다.")
        self._write(profile_id, users)

    @staticmethod
    def draft(source=DEFAULT_PROFILE, *, name="새 프로파일"):
        return replace(source, id=f"user.{uuid4()}", name=name, origin="user", revision=1)

    def save(self, profile):
        default, users = self.load()
        if profile.origin != "user":
            raise ValueError("내장 프로파일은 복제한 뒤 수정할 수 있습니다.")
        if profile.id in users:
            previous = users[profile.id]
            if profile.revision != previous.revision:
                raise ValueError("프로파일이 다른 곳에서 변경되었습니다. 다시 열어 주세요.")
            if profile == previous:
                return previous
            profile = replace(profile, revision=previous.revision + 1)
        users[profile.id] = profile
        self._write(default, users)
        return profile

    def delete(self, profile_id, *, project_default=None, block_references=()):
        default, users = self.load()
        if profile_id not in users:
            raise ValueError("사용자 프로파일만 삭제할 수 있습니다.")
        if profile_id == default:
            raise ValueError("앱 기본값으로 사용 중입니다. 앱 기본값을 먼저 바꾸세요.")
        if profile_id == project_default:
            raise ValueError("현재 프로젝트 기본값으로 사용 중입니다. 프로젝트 기본값을 먼저 바꾸세요.")
        if profile_id in block_references:
            raise ValueError("현재 프로젝트 블록에서 사용 중입니다. 블록의 프로파일을 먼저 바꾸세요.")
        del users[profile_id]
        self._write(default, users)


def project_motion_profiles(project):
    settings = project.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("프로젝트 마우스 설정 형식이 잘못되었습니다.")
    default = settings.get(DEFAULT_KEY)
    snapshots = _user_profiles(project.get(SNAPSHOTS_KEY, []))
    if default is not None and (not isinstance(default, str) or default not in (BUILTIN_PROFILES | snapshots)):
        raise ValueError("프로젝트 기본 모션 프로파일이 없거나 스냅샷을 찾을 수 없습니다.")
    return default, snapshots


def set_project_motion_profile(project, profile):
    _, snapshots = project_motion_profiles(project)
    settings = dict(project.get("settings", {}))
    if profile is None:
        settings.pop(DEFAULT_KEY, None)
    else:
        settings[DEFAULT_KEY] = profile.id
        if profile.origin == "user":
            snapshots[profile.id] = profile
    project["settings"] = settings
    project[SNAPSHOTS_KEY] = [snapshot.to_dict() for snapshot in snapshots.values()]


def motion_block_references(workspace):
    references = set()
    blocks = list(workspace.get("blocks", {}).get("blocks", []))
    while blocks:
        block = blocks.pop()
        if block.get("type") in ("ms_mouse_move_timed", "ms_mouse_move_relative_timed"):
            profile_id = block.get("fields", {}).get("PROFILE")
            if isinstance(profile_id, str) and profile_id.startswith("user."):
                references.add(profile_id)
        for connection in (*block.get("inputs", {}).values(), block.get("next", {})):
            child = connection.get("block") or connection.get("shadow")
            if child is not None:
                blocks.append(child)
    return references


def capture_motion_profiles(project, users, *, references=()):
    _, snapshots = project_motion_profiles(project)
    for profile_id in sorted(motion_block_references(project["workspace"]) | set(references)):
        if profile_id in users:
            snapshots.setdefault(profile_id, users[profile_id])
    if snapshots:
        project[SNAPSHOTS_KEY] = [profile.to_dict() for profile in snapshots.values()]
