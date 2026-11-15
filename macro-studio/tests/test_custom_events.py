from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.runtime import Runtime


def block(kind, identifier, fields=None, **inputs):
    return {"type": "ms_" + kind, "id": identifier, "fields": fields or {},
            "inputs": {name: {"block": child} for name, child in inputs.items()}}


def program():
    return compile_project({"project_id": "events", "workspace": {"blocks": {"blocks": [
        block("start", "start", BODY=block("emit_event", "emit", {"NAME": " ready "})),
        block("custom", "receive", {"NAME": "ready"},
              BODY=block("call", "call", {"NAME": "tap"})),
        block("custom", "other", {"NAME": "other"},
              BODY=block("key_tap", "other-key", {"KEY": "B", "MS": 2})),
        block("workflow", "tap", {"NAME": "tap"},
              BODY=block("key_tap", "key", {"KEY": "A", "MS": 2})),
    ]}}})[0]


def test_publication_survives_publisher_completion_and_runs_matching_workflow():
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program(), clock, backend)
    runtime.run(10_000_000, [])
    assert not runtime.errors
    assert backend.events == [
        {"time_ns": 1_000_000, "action": "key_down", "key": "scan:1E"},
        {"time_ns": 3_000_000, "action": "key_up", "key": "scan:1E"},
    ]


def test_stop_discards_undelivered_event_and_new_session_starts_clean():
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program(), clock, backend)
    runtime.run(1_000_000, [])
    assert not backend.events
    runtime.run(10_000_000, [])
    assert [event["action"] for event in backend.events] == ["key_down", "key_up"]


def test_receiver_guard_is_checked_at_delivery():
    code = program()
    code["handlers"][1]["guard"] = False
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(10_000_000, [])
    assert not runtime.errors and not backend.events
