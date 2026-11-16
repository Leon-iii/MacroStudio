import json
from dataclasses import replace

import pytest

from macro_studio.mouse_motion import DEFAULT_PROFILE, DEFAULT_PROFILE_ID
from macro_studio.mouse_motion.registry import (
    MotionProfileRegistry, project_motion_profiles, set_project_motion_profile,
)


def test_default_and_users_persist_while_project_keeps_its_profile_revision(tmp_path):
    path = tmp_path / "mouse-motion.json"
    registry = MotionProfileRegistry(path)
    assert registry.load() == (DEFAULT_PROFILE_ID, {})
    original = registry.save(registry.draft(DEFAULT_PROFILE, name="업무용"))
    copied = registry.save(registry.draft(original, name="업무용 복사본"))
    assert original.id != copied.id
    project = {}
    set_project_motion_profile(project, original)
    registry.set_default(original.id)

    edited = registry.save(replace(original, name="업무용 수정", duration_ms=870))
    reloaded = MotionProfileRegistry(path)
    assert edited.revision == 2
    assert reloaded.load() == (original.id, {original.id: edited, copied.id: copied})
    assert project_motion_profiles(json.loads(json.dumps(project))) == (original.id, {original.id: original})
    with pytest.raises(ValueError, match="다른 곳에서"):
        registry.save(replace(original, duration_ms=890))
    with pytest.raises(ValueError, match="앱 기본값"):
        reloaded.delete(original.id)
    reloaded.set_default(DEFAULT_PROFILE_ID)
    with pytest.raises(ValueError, match="프로젝트 기본값"):
        reloaded.delete(original.id, project_default=original.id)
    reloaded.delete(original.id)
    assert project_motion_profiles(project)[1][original.id] == original
    assert MotionProfileRegistry(path).load() == (DEFAULT_PROFILE_ID, {copied.id: copied})


def test_corrupted_store_is_preserved_and_builtins_cannot_be_changed(tmp_path):
    path = tmp_path / "mouse-motion.json"
    registry = MotionProfileRegistry(path)
    with pytest.raises(ValueError, match="내장 프로파일"):
        registry.save(replace(DEFAULT_PROFILE, duration_ms=125))
    assert not path.exists()
    path.write_text("malformed", encoding="utf-8")
    with pytest.raises(ValueError):
        registry.save(registry.draft())
    assert path.read_text(encoding="utf-8") == "malformed"
