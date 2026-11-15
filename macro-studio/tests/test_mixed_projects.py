import json
from pathlib import Path

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.runtime import Runtime


def run_example(name, duration_ms, *hotkey_ms):
    path = Path(__file__).resolve().parents[1] / "examples" / f"{name}.macroproj"
    program, detached = compile_project(json.loads(path.read_text(encoding="utf-8")))
    assert not detached
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    runtime.run(duration_ms * 1_000_000, [(ms * 1_000_000, "CTRL+F7") for ms in hotkey_ms])
    return [(e["time_ns"] // 1_000_000, e["action"], e["key"]) for e in backend.events], runtime.errors


def test_hotkey_runs_between_automatic_iterations():
    events, errors = run_example("mixed-loop", 460, 80)
    assert not errors
    assert [ms for ms, action, key in events if action == "key_down" and key == "scan:30"] == [0, 80]
    assert [ms for ms, action, key in events if action == "key_down" and key == "scan:1E"] == [50, 250, 450]
    assert events[-1] == (460, "key_up", "scan:1E")


def test_conflict_preserves_owners_input_until_stop():
    events, errors = run_example("key-collision", 300, 80)
    assert [(e["code"], e["block_id"]) for e in errors] == [("input_conflict", "conflicting-a")]
    assert events == [(0, "key_down", "scan:1E"), (300, "key_up", "scan:1E")]


def test_restart_releases_before_repress_and_discards_old_deadline():
    events, errors = run_example("hotkey-restart", 3250, 50, 300)
    assert not errors
    assert events == [(50, "key_down", "scan:2E"), (300, "key_up", "scan:2E"),
                      (300, "key_down", "scan:2E"), (3250, "key_up", "scan:2E")]
