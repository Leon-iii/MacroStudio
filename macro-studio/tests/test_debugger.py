import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace

import pytest

from macro_studio.compiler import compile_project


def debug_project():
    actions = [{"type": "ms_key_tap", "id": key, "fields": {"KEY": key, "MS": 5}}
               for key in "ABC"]
    actions[0]["data"] = "macro-studio:breakpoint"
    for first, second in zip(actions, actions[1:]):
        first["next"] = {"block": second}
    start = {"type": "ms_start", "id": "start", "inputs": {"BODY": {"block": actions[0]}}}
    return {"project_id": "debug", "workspace": {"blocks": {"blocks": [start]}}}


def check_runner(first_command):
    program, _ = compile_project(debug_project())
    source = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(source)}
    messages = Queue()
    edges = []
    with subprocess.Popen([sys.executable, "-m", "macro_studio.editor_runner"], env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as runner:
        def read():
            for line in runner.stdout:
                messages.put(json.loads(line))

        def send(message):
            runner.stdin.write(message + b"\n")
            runner.stdin.flush()

        def until(kind, **fields):
            while True:
                item = messages.get(timeout=5)
                assert item["kind"] != "error", item
                if item["kind"] == "inputs":
                    edges.extend(key for _, key in item["events"])
                if item["kind"] == kind and all(item.get(k) == v for k, v in fields.items()):
                    return

        Thread(target=read, daemon=True).start()
        try:
            send(json.dumps({"program": program, "target": None, "debug": True}).encode())
            until("breakpoint", block_id="A")
            assert not edges
            send(first_command)
            if first_command == b"step":
                until("breakpoint", block_id="B")
                assert edges == ["scan:1E"] * 2
                send(b"continue")
            until("state", state="STOPPED")
            assert runner.wait(timeout=5) == 0
            return edges
        finally:
            if runner.poll() is None:
                runner.kill()


def test_breakpoint_then_step_then_continue():
    assert check_runner(b"step") == ["scan:1E"] * 2 + ["scan:30"] * 2 + ["scan:2E"] * 2


def test_stop_at_breakpoint_does_not_execute_block():
    assert check_runner(b"stop") == []


def test_editor_routes_execution_highlights_and_breakpoints_as_distinct_messages():
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from macro_studio.gui import Studio

    messages = [
        {"kind": "active_blocks", "block_ids": ["first", "second"]},
        {"kind": "active_blocks", "block_ids": []},
        {"kind": "breakpoint", "block_id": "third"},
    ]
    wire = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    active, focused, logs, states = [], [], [], []
    editor = SimpleNamespace(
        session_state="RUNNING", pending_output=b"", log=logs.append,
        process=SimpleNamespace(readAllStandardOutput=lambda: wire),
        highlight_python=lambda block_ids: None,
        bridge=SimpleNamespace(activeBlocksChanged=SimpleNamespace(emit=active.append),
                               focusBlock=SimpleNamespace(emit=focused.append)),
    )
    editor.refresh = lambda: states.append(editor.session_state)
    Studio.read_runner(editor)
    assert active == [["first", "second"], []]
    assert focused == ["third"] and states == ["BREAKPOINT"]
    assert logs == ["브레이크포인트 · third"]
