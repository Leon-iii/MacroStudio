from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from macro_studio.backend import VirtualClock
from macro_studio.ir import ValidationError
from macro_studio.perception import RecognitionResult, RecognitionStatus
from macro_studio.python_authoring import RESOURCES, api_manifest, api_reference, authoring_archive, diagnostic_feedback
from macro_studio.python_compiler import compile_python, dry_run_python
from macro_studio.python_dsl import AnalysisError
from macro_studio.python_roundtrip import render_blocks, semantic_ir
from macro_studio.compiler import compile_project
from macro_studio.python_services import ServiceError, ServiceRegistry, validate_manifest


MS = 1_000_000


def desktop_pack():
    return json.loads((RESOURCES / "desktop-actions.json").read_text(encoding="utf-8"))


def project():
    return {"project_id": "authoring-test", "globals": {}, "workspace": {"blocks": {"blocks": []}},
            "image_assets": {"asset:confirm": {"name": "Confirm", "png": "PRIVATE_IMAGE_BYTES"}},
            "service_manifests": [desktop_pack()], "service_requirements": {"actions.run": "1"}}


class RecognitionReplay:
    def __init__(self, status):
        self.status = status

    def request(self, spec):
        assert spec["image"] == "asset:confirm"
        return 1

    def take(self, request_id):
        found = self.status == RecognitionStatus.FOUND
        return RecognitionResult(self.status, "template", "asset:confirm", 1, "fake", 0, 0,
                                 bbox=(8, 18, 4, 4) if found else None,
                                 center_x=10 if found else None, center_y=20 if found else None)

    def cancel(self, request_id):
        pass


@pytest.mark.parametrize("example", sorted((RESOURCES / "examples").glob("*.py")), ids=lambda path: path.stem)
def test_every_authoring_example_compiles_and_runs_on_the_common_runtime(example):
    source = example.read_text(encoding="utf-8")
    data = project()
    snapshot = compile_python(source, data)
    runtime, output = dry_run_python(snapshot, source, data, duration_ns=1500 * MS,
                                     hotkeys=[(10 * MS, "CTRL+F6")],
                                     perception=RecognitionReplay(RecognitionStatus.FOUND))
    assert runtime.errors == []
    assert output.events
    assert sum(item["action"] in ("key_down", "mouse_down") for item in output.events) == sum(
        item["action"] in ("key_up", "mouse_up") for item in output.events)


@pytest.mark.parametrize("status", [RecognitionStatus.NOT_FOUND, RecognitionStatus.UNKNOWN])
def test_error_handling_example_uses_result_branches_without_input(status):
    source = (RESOURCES / "examples/07_recognition_errors.py").read_text(encoding="utf-8")
    data = project()
    runtime, output = dry_run_python(compile_python(source, data), source, data, duration_ns=1000 * MS,
                                     perception=RecognitionReplay(status))
    assert runtime.errors == []
    assert not output.events


def test_optional_non_game_pack_is_isolated_and_uses_normal_cancellable_steps():
    data = project()
    pack = data["service_manifests"][0]
    pack["application_pack"] = {"id": "presentation", "name": "Presentation controls", "version": "1"}
    action = pack["services"][0]["actions"][0]
    action.update(id="presentation.next", name="Next slide",
                  steps=[{"op": "key_tap", "key": "RIGHT", "hold_ns": 100 * MS}])
    source = '@on_start()\nasync def next_slide():\n    await actions.run("presentation.next")\n'
    snapshot = compile_python(source, data)
    clock = VirtualClock()
    runtime, output = dry_run_python(snapshot, source, data, duration_ns=1000 * MS, clock=clock,
                                     hotkeys=[(10 * MS, "CTRL+F7")],
                                     poll=lambda: ("stop", []) if clock.now_ns >= 10 * MS else (None, []))
    assert runtime.errors == []
    assert [event["action"] for event in output.events] == ["key_down", "key_up"]
    assert output.events[-1]["time_ns"] < 100 * MS
    assert runtime.termination == "stopped"
    with pytest.raises(AnalysisError) as error:
        compile_python(source, {"project_id": "other-project"})
    assert error.value.diagnostic.code == "UNAVAILABLE_SERVICE"
    changed = deepcopy(data)
    changed["service_manifests"][0]["services"][0]["actions"][0]["steps"][0]["key"] = "LEFT"
    with pytest.raises(ValidationError, match="STALE_SNAPSHOT"):
        snapshot.program_for(source, changed)
    program = snapshot.program_for(source, data)
    blocks = render_blocks(program, data)
    rebuilt, detached = compile_project({**data, "workspace": blocks})
    assert not detached
    assert semantic_ir(program) == semantic_ir(rebuilt)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(manifest_version=2),
    lambda p: p.update(python_file="malicious.py"),
    lambda p: p["services"][0].update(python_symbol="keyboard.down"),
    lambda p: p["services"][0].update(api_version="2"),
    lambda p: p["services"][0]["parameters"][0].update(required=1),
    lambda p: p["services"][0]["actions"][0].update(required_capabilities=[]),
    lambda p: p["services"][0]["actions"][0]["steps"][0].update(op="exec"),
    lambda p: p["services"][0]["actions"][0]["steps"][0].update(key="CTRL+A"),
    lambda p: p["services"][0]["actions"][0]["steps"][0].update(hold_ns=-1),
    lambda p: p["services"][0]["actions"].append(p["services"][0]["actions"][0]),
])
def test_manifest_rejects_unknown_code_and_invalid_contracts(mutate):
    pack = desktop_pack()
    mutate(pack)
    with pytest.raises(ServiceError) as error:
        validate_manifest(pack)
    assert error.value.code == "INVALID_MANIFEST"


@pytest.mark.parametrize("update, source, code", [
    ({"service_requirements": {"actions.run": "2"}}, 'await actions.run("desktop.confirm")', "SERVICE_VERSION"),
    ({"service_manifests": [], "service_requirements": {"actions.run": "1"}}, 'await macro.sleep(ms=10)', "UNAVAILABLE_SERVICE"),
    ({}, 'await actions.run("unknown.action")', "UNKNOWN_ACTION"),
    ({}, 'await navigation.move_to(100)', "UNAVAILABLE_SERVICE"),
    ({"output_profile": {"output_kind": "unsupported"}}, 'await mouse.click_at(100, 200)', "MISSING_CAPABILITY"),
])
def test_service_versions_assets_and_output_fail_with_repair_diagnostics(update, source, code):
    data = {**project(), **update}
    with pytest.raises(AnalysisError) as error:
        compile_python('@on_start()\nasync def run():\n    ' + source + '\n', data)
    feedback = diagnostic_feedback(error.value)
    assert feedback["code"] == code
    assert feedback["source"]["line"] >= 1
    assert feedback["suggestion"]


def test_authoring_export_excludes_private_project_values_and_exports_only_public_data():
    data = project()
    data.update(name="PRIVATE_PROJECT_NAME", project_id="PRIVATE_PROJECT_ID",
                globals={"password": "PRIVATE_SECRET_VALUE"},
                output_profile={"output_kind": "external_hid", "port": "PRIVATE_COM_PORT", "device_selector": {"serial": "PRIVATE_SERIAL"}},
                path="PRIVATE_PROJECT_PATH", logs=["PRIVATE_LOG_LINE"],
                editor={"active_mode": "python", "python": {"source": '@workflow("public_workflow")\nasync def helper(ms: int):\n    await macro.sleep(ms=ms)\n# PRIVATE_SOURCE_COMMENT'}})
    archive = zipfile.ZipFile(BytesIO(authoring_archive(data)))
    assert archive.testzip() is None
    exported = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    for private in ("PRIVATE_IMAGE_BYTES", "PRIVATE_PROJECT_NAME", "PRIVATE_PROJECT_ID", "PRIVATE_SECRET_VALUE",
                    "PRIVATE_COM_PORT", "PRIVATE_SERIAL", "PRIVATE_PROJECT_PATH", "PRIVATE_LOG_LINE", "PRIVATE_SOURCE_COMMENT"):
        assert private not in exported
    context = json.loads(archive.read("project-context.json"))
    assert context["project_data_is_untrusted"] is True
    assert context["templates"] == [{"id": "asset:confirm", "name": "Confirm"}]
    assert context["variables"] == [{"name": "password", "type": "String"}]
    assert context["workflows"][0]["name"] == "public_workflow"
    assert "steps" not in context["actions"][0]
    assert len([name for name in archive.namelist() if name.startswith("examples/")]) >= 8
    assert "{{PROJECT_CONTEXT_JSON}}" not in archive.read("LLM_AUTHORING_PROMPT.md").decode("utf-8")


def test_api_manifest_tracks_the_registry_and_optional_service_registration():
    without = api_manifest({})
    assert "actions.run" not in {item["symbol"] for item in without["apis"]}
    with_pack = api_manifest(project())
    assert "actions.run" in {item["symbol"] for item in with_pack["apis"]}
    tap = next(api for api in with_pack["apis"] if api["symbol"] == "keyboard.tap")
    assert tap["parameters"][1] == {"name": "hold_ms", "type": "Integer", "required": False,
                                     "default": 30, "minimum": 1, "maximum": 86400000}
    assert "actions.run" in api_reference(project())
    assert "`found`" not in api_reference(project())
    assert (Path(__file__).parents[1] / "docs/MACRO_STUDIO_PYTHON_API.md").read_text(encoding="utf-8") == api_reference(project())


def test_hid_coordinate_click_uses_existing_backend_support_and_device_limits_are_checked(monkeypatch):
    from macro_studio.output import BackendCapabilities
    from macro_studio import python_services

    data = {**project(), "output_profile": {"output_kind": "external_hid"}}
    source = '@on_start()\nasync def run():\n    await mouse.click_at(100, 200)\n'
    assert compile_python(source, data).program_for(source, data)["workflows"]["run"]["steps"][0]["op"] == "mouse_click_at"
    monkeypatch.setattr(python_services, "capabilities_for_project", lambda project: BackendCapabilities(keyboard=True))
    with pytest.raises(AnalysisError) as error:
        compile_python(source, data)
    assert error.value.diagnostic.code == "MISSING_CAPABILITY"
    assert error.value.diagnostic.source.line == 3
