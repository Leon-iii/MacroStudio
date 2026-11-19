"""Declarative Application Packs lowered to existing, cancellable IR steps."""

from copy import deepcopy
import json
import re

from .ir import MAX_DURATION_NS, validate_program
from .output import BackendCapabilities, DESKTOP_CAPABILITIES, FAKE_CAPABILITIES
from .python_dsl import API_REGISTRY, Api, Parameter


MANIFEST_LIMIT = 128 * 1024
ACTION_API = Api("repeat", (Parameter("action_id", str),))
SERVICE_CONTRACT = {
    "service_id": "actions.run", "api_version": "1", "python_symbol": "actions.run",
    "kind": "async_action", "parameters": [{"name": "action_id", "type": "String", "required": True}],
    "returns": "None", "ir_opcode": "repeat", "required_capabilities": [], "interruptible": True,
}
CAPABILITIES = frozenset({"output.keyboard", "output.mouse"})
STEP_FIELDS = {
    "key_tap": {"op", "key", "hold_ns"},
    "mouse_click": {"op", "button", "hold_ns"},
    "wait": {"op", "duration_ns"},
}


class ServiceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _invalid(message):
    raise ServiceError("INVALID_MANIFEST", message)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", value) is not None


def _label(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 128


def capabilities_for_project(project):
    output = project.get("output_profile", {})
    if not isinstance(output, dict):
        raise ServiceError("MISSING_CAPABILITY", "출력 프로필이 잘못되었습니다.")
    kind = output.get("output_kind", "fake")
    if kind == "fake":
        return FAKE_CAPABILITIES
    if kind == "sendinput":
        return DESKTOP_CAPABILITIES
    if kind == "external_hid":
        # These are the protocol's baseline features. Device availability is checked at ARM/START.
        return BackendCapabilities(keyboard=True, mouse_buttons=True, mouse_relative=True, mouse_absolute=True,
                                   profiled_mouse=True, timed_batch=True, onboard_tap_train=True)
    raise ServiceError("MISSING_CAPABILITY", "지원하지 않는 출력 방식입니다.")


def capability_names(capabilities):
    names = set()
    if capabilities.keyboard:
        names.add("output.keyboard")
    if capabilities.mouse_buttons:
        names.add("output.mouse")
    return names


def validate_manifest(raw):
    try:
        size = len(json.dumps(raw, ensure_ascii=True, allow_nan=False).encode("ascii"))
    except (ValueError, TypeError, RecursionError):
        _invalid("Manifest는 유한한 JSON 데이터여야 합니다.")
    if size > MANIFEST_LIMIT or not isinstance(raw, dict):
        _invalid("Manifest의 형식 또는 128 KiB 크기 한도를 확인하세요.")
    if set(raw) != {"manifest_version", "application_pack", "services"} or type(raw["manifest_version"]) is not int or raw["manifest_version"] != 1:
        _invalid("Manifest 버전 1과 정해진 필드만 지원합니다.")
    pack = raw["application_pack"]
    if (not isinstance(pack, dict) or set(pack) != {"id", "name", "version"} or
            not _identifier(pack["id"]) or not _label(pack["name"]) or pack["version"] != "1"):
        _invalid("Application Pack의 ID·이름·버전 1을 확인하세요.")
    services = raw["services"]
    if not isinstance(services, list) or len(services) != 1:
        _invalid("현재 팩은 actions.run 서비스 하나를 제공해야 합니다.")
    service = services[0]
    if (not isinstance(service, dict) or set(service) != {*SERVICE_CONTRACT, "actions"} or
            any(json.dumps(service.get(key), sort_keys=True) != json.dumps(value, sort_keys=True)
                for key, value in SERVICE_CONTRACT.items())):
        _invalid("지원하는 actions.run 버전 1의 선언형 계약을 사용하세요.")
    actions = service["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 64:
        _invalid("팩에는 액션 1~64개가 필요합니다.")
    ids = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"id", "name", "required_capabilities", "steps"}:
            _invalid("액션의 필드가 잘못되었습니다.")
        if not _identifier(action["id"]) or action["id"] in ids or not _label(action["name"]):
            _invalid("액션 ID는 팩 안에서 고유해야 하며 공개 이름이 필요합니다.")
        ids.add(action["id"])
        requirements = action["required_capabilities"]
        if (not isinstance(requirements, list) or any(not isinstance(item, str) or item not in CAPABILITIES for item in requirements)
                or len(set(requirements)) != len(requirements)):
            _invalid("액션의 출력 요구 기능이 잘못되었습니다.")
        steps = action["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
            _invalid("액션에는 단순 IR 명령 1~64개가 필요합니다.")
        required = set()
        duration = 0
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("op"), str) or set(step) != STEP_FIELDS.get(step["op"]):
                _invalid("액션에는 key_tap, mouse_click, wait의 정해진 필드만 사용할 수 있습니다.")
            if step["op"] == "key_tap":
                required.add("output.keyboard")
            elif step["op"] == "mouse_click":
                required.add("output.mouse")
            ns = step.get("hold_ns", step.get("duration_ns"))
            if type(ns) is not int or not 0 <= ns <= MAX_DURATION_NS:
                _invalid("명령 시간은 허용 범위 안의 정수 나노초여야 합니다.")
            duration += ns
        if required != set(requirements) or duration > MAX_DURATION_NS:
            _invalid("선언한 출력 요구 기능은 명령과 일치해야 하며 액션 시간 한도를 지켜야 합니다.")
        try:
            validate_program({"ir_version": 1, "project_id": "manifest-check", "globals": {},
                              "workflows": {"action": {"steps": deepcopy(steps)}}, "handlers": []})
        except ValueError as error:
            _invalid(str(error))
    return deepcopy(raw)


class ServiceRegistry:
    def __init__(self, project):
        raw = project.get("service_manifests", [])
        if not isinstance(raw, list) or len(raw) > 8:
            _invalid("Application Pack은 프로젝트당 최대 8개를 등록할 수 있습니다.")
        self.manifests = []
        self.actions = {}
        self.pack_ids = set()
        self.apis = dict(API_REGISTRY)
        self.capabilities = capabilities_for_project(project)
        for item in raw:
            manifest = validate_manifest(item)
            pack_id = manifest["application_pack"]["id"]
            if pack_id in self.pack_ids:
                _invalid("Application Pack ID가 중복됩니다.")
            self.pack_ids.add(pack_id)
            for action in manifest["services"][0]["actions"]:
                if action["id"] in self.actions:
                    _invalid("프로젝트의 액션 ID가 중복됩니다.")
                self.actions[action["id"]] = action
            self.manifests.append(manifest)
        self.versions = {"actions.run": "1"} if self.manifests else {}
        if self.manifests:
            self.apis["actions.run"] = ACTION_API
        requirements = project.get("service_requirements", {})
        if not isinstance(requirements, dict):
            _invalid("필수 서비스 버전은 ID와 버전의 객체여야 합니다.")
        for name, version in requirements.items():
            if name not in self.versions:
                raise ServiceError("UNAVAILABLE_SERVICE", f"등록된 필수 서비스가 없습니다. {name}")
            if type(version) is not str or version != self.versions[name]:
                raise ServiceError("SERVICE_VERSION", f"필수 서비스 버전이 일치하지 않습니다. {name}")

    def action(self, action_id):
        if action_id not in self.actions:
            raise ServiceError("UNKNOWN_ACTION", f"등록된 액션 ID가 없습니다. {action_id}")
        action = self.actions[action_id]
        if not set(action["required_capabilities"]) <= capability_names(self.capabilities):
            raise ServiceError("MISSING_CAPABILITY", "선택한 출력 방식이 액션의 요구 기능을 지원하지 않습니다.")
        return deepcopy(action["steps"])
