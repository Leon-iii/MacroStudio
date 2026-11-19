import pytest

from macro_studio.python_dsl import API_REGISTRY, MAX_SOURCE_CHARS, AnalysisError, Reference, SourceSpan, analyze


def test_declarations_resolve_registered_calls_and_defaults():
    start, hotkey, interval, workflow = analyze(
        '@on_start\nasync def start():\n    await macro.sleep(ms=100)\n'
        '@on_hotkey("shift+ctrl+f6", reentrancy="restart")\nasync def tap():\n'
        '    await keyboard.tap("a")\n    await keyboard.repeat("B", 200, 2, 100)\n'
        '@on_interval(ms=1000)\nasync def click():\n    await mouse.click()\n'
        '@workflow("position")\nasync def move(x: int):\n'
        '    await mouse.move_to(x=x, y=-25, profile_id="builtin.natural")\n    await macro.stop()\n'
    )
    assert [item.kind for item in (start, hotkey, interval, workflow)] == [
        "project_start", "hotkey_pressed", "interval", "workflow",
    ]
    assert start.calls[0].source == SourceSpan(3, 10, 3, 29)
    assert dict(hotkey.options) == {"key": "CTRL+SHIFT+F6", "reentrancy": "restart", "priority": 0}
    assert dict(hotkey.calls[0].arguments) == {"key": "A", "hold_ms": 30}
    assert API_REGISTRY[hotkey.calls[1].symbol].opcode == "tap_sequence"
    assert dict(interval.options)["ms"] == 1000
    assert dict(interval.calls[0].arguments) == {"button": "left", "hold_ms": 50}
    assert workflow.name == "position"
    assert dict(workflow.calls[0].arguments) == {"x": Reference("x"), "y": -25, "profile_id": "builtin.natural"}


@pytest.mark.parametrize(("body", "code"), [
    ("import os", "UNSUPPORTED_SYNTAX"),
    ('await exec("unsafe")', "UNSUPPORTED_SYNTAX"),
    ('await eval("unsafe")', "UNSUPPORTED_SYNTAX"),
    ('await getattr(mouse, "click")()', "UNSUPPORTED_SYNTAX"),
    ("await mouse.__class__.click()", "UNSUPPORTED_SYNTAX"),
    ("await macro.sleep(ms=(lambda: 1)())", "UNSUPPORTED_SYNTAX"),
    ("class Arbitrary: pass", "UNSUPPORTED_SYNTAX"),
    ("while True: pass", "UNBOUNDED_LOOP"),
    ("await mouse.unregistered()", "UNKNOWN_API"),
    ('await actions.run("missing")', "UNAVAILABLE_SERVICE"),
    ("await macro.sleep(ms=True)", "TYPE_ERROR"),
    ("await macro.sleep(other=10)", "INVALID_ARGUMENT"),
    ("await macro.sleep(10, ms=20)", "INVALID_ARGUMENT"),
    ("await macro.sleep()", "INVALID_ARGUMENT"),
    ("await macro.sleep(-1)", "INVALID_ARGUMENT"),
    ('await keyboard.repeat("A", 200, 5, 2)', "INVALID_ARGUMENT"),
    ('await keyboard.tap("Ctrl+A")', "INVALID_ARGUMENT"),
    ("await mouse.move_to(x=missing, y=1)", "UNKNOWN_NAME"),
])
def test_rejected_body_reports_code_and_line(body, code):
    with pytest.raises(AnalysisError) as found:
        analyze(f"@on_start\nasync def main():\n    {body}\n")
    assert found.value.diagnostic.code == code
    assert found.value.diagnostic.source.line == 3


def test_workflow_symbols_are_typed_and_duplicates_are_rejected():
    with pytest.raises(AnalysisError) as found:
        analyze("@workflow\nasync def local(delay: str):\n    await macro.sleep(delay)")
    assert found.value.diagnostic.code == "TYPE_ERROR"
    with pytest.raises(AnalysisError) as found:
        analyze('@on_hotkey("F6")\nasync def one(): pass\n@on_hotkey("f6")\nasync def two(): pass')
    assert found.value.diagnostic.code == "DUPLICATE_DECLARATION"
    with pytest.raises(AnalysisError) as found:
        analyze("@workflow\nasync def local(x: int, x: int): pass")
    assert found.value.diagnostic.code == "DUPLICATE_DECLARATION"


def test_syntax_errors_and_resource_limits_are_diagnostics():
    with pytest.raises(AnalysisError) as found:
        analyze('@on_start\nasync def main():\n    await macro.sleep(ms=)')
    assert found.value.diagnostic.code == "SYNTAX_ERROR"
    assert found.value.diagnostic.source.line == 3
    for source in (" " * (MAX_SOURCE_CHARS + 1), "@on_start\nasync def main():\n" + "    pass\n" * 4100):
        with pytest.raises(AnalysisError) as found:
            analyze(source)
        assert found.value.diagnostic.code == "SOURCE_LIMIT"


def test_analysis_does_not_execute_source(tmp_path):
    marker = tmp_path / "executed"
    payload = f"open({str(marker)!r}, 'w').close()"
    source = f"@on_start\nasync def main():\n    await exec({payload!r})"
    with pytest.raises(AnalysisError):
        analyze(source)
    assert not marker.exists()
