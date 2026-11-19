import ctypes
from types import SimpleNamespace

import pytest

from macro_studio.python_compiler import compile_python
from macro_studio.python_dsl import AnalysisError
from macro_studio.python_roundtrip import generate_python
from macro_studio.win32 import WindowsDesktop


def test_canonical_python_generation_is_repeatable():
    source = '@on_hotkey("CTRL+F6")\nasync def work():\n    await keyboard.tap("A")\n'
    project = {"project_id": "determinism"}
    program = compile_python(source, project).program_for(source, project)
    first = generate_python(program, project)
    second = generate_python(program, project)
    assert first.source == second.source
    assert first.block_ids_by_line == second.block_ids_by_line


def test_deep_workflow_calls_fail_with_source_diagnostic():
    declarations = [f'@workflow("w{i}")\nasync def w{i}():\n    await workflow.call("w{i + 1}")\n'
                    for i in range(34)]
    declarations.append('@workflow("w34")\nasync def last():\n    await macro.sleep(ms=1)\n')
    with pytest.raises(AnalysisError) as error:
        compile_python("\n".join(declarations), {"project_id": "deep-calls"})
    assert error.value.diagnostic.code == "CALL_DEPTH"
    assert error.value.diagnostic.source.line > 0


def test_physical_cursor_and_target_checks_are_independent_of_callers_dpi_context():
    desktop = object.__new__(WindowsDesktop)
    api = SimpleNamespace(context=1, cursor=(2200, 300))
    physical = ctypes.c_void_p(-4).value

    def context(value):
        previous = api.context
        api.context = value.value if isinstance(value, ctypes.c_void_p) else value
        return previous

    def cursor(pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(ctypes.wintypes.POINT)).contents
        point.x, point.y = api.cursor if api.context == physical else (1760, 240)
        return True

    def move(x, y):
        api.cursor = (x, y) if api.context == physical else (round(x * 1.25), round(y * 1.25))
        return True

    desktop._set_dpi_context = context
    desktop._cursor = cursor
    desktop._set_cursor = move
    desktop._window_at = lambda point: 42 if api.context == physical and (point.x, point.y) == api.cursor else None
    assert desktop.mouse_position() == (2200, 300)
    assert desktop.point_over(SimpleNamespace(hwnd=42), (2200, 300))
    desktop.send_mouse_move((2300, 400))
    assert desktop.mouse_position() == (2300, 400)
    assert api.context == 1
