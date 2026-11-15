import pytest

from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError


def project(*blocks):
    return {"project_id": "sample", "workspace": {"blocks": {"blocks": list(blocks)}}}


def test_compiles_hat_with_actions_and_reports_detached_blocks():
    step = {"type": "ms_key_tap", "id": "tap-a", "fields": {"KEY": "A", "MS": 2}}
    hat = {"type": "ms_start", "id": "startup", "inputs": {"BODY": {"block": step}}}
    ir, detached = compile_project(project(hat, {"type": "ms_wait", "id": "loose"}))
    assert detached == ["loose"]
    assert ir["handlers"][0]["event"] == {"type": "project_start"}
    assert ir["workflows"]["@startup"]["steps"] == [
        {"op": "key_tap", "block_id": "tap-a", "key": "scan:1E", "hold_ns": 2_000_000},
    ]


def test_invalid_hotkey_error_names_block():
    with pytest.raises(ValidationError, match=r"\(trigger\)"):
        compile_project(project({"type": "ms_hotkey", "id": "trigger", "fields": {"KEY": "broken"}}))
