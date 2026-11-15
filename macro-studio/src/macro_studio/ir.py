import math
import operator
import re

MAX_REPEAT_COUNT = 1_000_000
MAX_CALL_DEPTH = 32
MAX_DURATION_NS = 86_400_000_000_000
DEFAULT_STOP_HOTKEY = "CTRL+ALT+SHIFT+F11"
SCREEN_SENSOR_OPS = ("pixel_color", "region_image", "region_text")
OCR_LANGUAGES = ("eng", "kor", "kor+eng")
VALUE_TYPES = {"number": int, "boolean": bool, "string": str}
PARAM_PREFIX = "@arg:"

_BINARY = {
    "add": operator.add, "sub": operator.sub, "mul": operator.mul, "div": operator.truediv,
    "eq": operator.eq, "ne": operator.ne, "lt": operator.lt, "le": operator.le,
    "gt": operator.gt, "ge": operator.ge,
}
_LETTERS = ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM")
_SCAN_CODES = {
    letter: start + index
    for row, start in zip(_LETTERS, (0x10, 0x1E, 0x2C))
    for index, letter in enumerate(row)
}
_SCAN_CODES.update({digit: 2 + index for index, digit in enumerate("1234567890")})
_SCAN_CODES.update({f"F{i}": 0x3A + i for i in range(1, 11)})
_SCAN_CODES.update(F11=0x57, F12=0x58, ENTER=0x1C, SPACE=0x39, TAB=0x0F, ESC=1)
_MODIFIERS = ("CTRL", "ALT", "SHIFT", "WIN")


class ValidationError(ValueError):
    pass


def normalize_hotkey(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("hotkey must be a string")
    *modifiers, key = [part.strip().upper() for part in value.split("+")]
    if (key not in _SCAN_CODES or set(modifiers) - set(_MODIFIERS)
            or len(set(modifiers)) != len(modifiers)):
        raise ValidationError(f"invalid hotkey: {value}")
    return "+".join([mod for mod in _MODIFIERS if mod in modifiers] + [key])


def _key(value: object, path: str) -> str:
    if isinstance(value, str) and value.strip().upper() in _SCAN_CODES:
        return f"scan:{_SCAN_CODES[value.strip().upper()]:02X}"
    if isinstance(value, dict):
        code = value.get("code")
        extended = value.get("extended", False)
        if (value.get("kind") == "scan_code" and type(code) is int
                and 1 <= code <= 0x7F and type(extended) is bool):
            prefix = "E0" if extended else ""
            return f"scan:{prefix}{code:02X}"
    raise ValidationError(f"{path}: invalid key")


def _scalar_type(value: object, path: str) -> type:
    kind = type(value)
    if kind is float and math.isfinite(value):
        return int
    if kind in (int, bool, str):
        return kind
    raise ValidationError(f"{path}: expected a finite number, boolean or string")


def _expression(value: object, variables: dict, path: str, depth: int = 0) -> type:
    if depth > 32:
        raise ValidationError(f"{path}: expression nesting exceeds 32")
    if not isinstance(value, dict):
        return _scalar_type(value, path)
    if set(value) == {"var"}:
        name = value["var"]
        if not isinstance(name, str) or name not in variables:
            raise ValidationError(f"{path}: unknown variable {name!r}")
        return variables[name]
    op, args = value.get("op"), value.get("args")
    if op == "target_window":
        if value.get("state") not in ("foreground", "exists", "minimized"):
            raise ValidationError(f"{path}: invalid target window state")
        return bool
    if op in SCREEN_SENSOR_OPS:
        label = f"{path} ({_name(value.get('block_id'), path)})"
        _integer(value.get("x"), -32768, 32767, label)
        _integer(value.get("y"), -32768, 32767, label)
        if op == "pixel_color":
            color = value.get("color")
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValidationError(f"{label}: expected #RRGGBB")
        else:
            _integer(value.get("width"), 1, 1024, label)
            _integer(value.get("height"), 1, 1024, label)
            if op == "region_image":
                _name(value.get("image"), label)
            else:
                text = _name(value.get("text"), label)
                if len(text) > 512 or value.get("language") not in OCR_LANGUAGES:
                    raise ValidationError(f"{label}: invalid OCR language or text exceeds 512 characters")
        return bool
    if not isinstance(op, str) or not isinstance(args, list):
        raise ValidationError(f"{path}: invalid expression")
    if op not in _BINARY and op not in ("and", "or", "not"):
        raise ValidationError(f"{path}: unsupported expression {op!r}")
    if len(args) != (1 if op == "not" else 2):
        raise ValidationError(f"{path}: invalid argument count for {op}")
    types = [_expression(arg, variables, path, depth + 1) for arg in args]
    if op in ("and", "or", "not"):
        valid, result = all(kind is bool for kind in types), bool
    elif op in ("eq", "ne"):
        valid, result = types[0] is types[1], bool
    else:
        valid = types == [int, int]
        result = bool if op in ("lt", "le", "gt", "ge") else int
    if not valid:
        raise ValidationError(f"{path}: invalid operand types for {op}")
    return result


def evaluate(value, variables: dict, sensors=None, window=None):
    if not isinstance(value, dict):
        return value
    if "var" in value:
        return variables[value["var"]]
    op = value["op"]
    if op == "target_window":
        return window is not None and window(value["state"])
    if op in SCREEN_SENSOR_OPS:
        return sensors is not None and sensors(value["block_id"])
    args = value["args"]
    left = evaluate(args[0], variables, sensors, window)
    if op == "not":
        return not left
    if op == "and":
        return left and evaluate(args[1], variables, sensors, window)
    if op == "or":
        return left or evaluate(args[1], variables, sensors, window)
    result = _BINARY[op](left, evaluate(args[1], variables, sensors, window))
    if type(result) is float and not math.isfinite(result):
        raise ValueError("expression result must be finite")
    return result


def _object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: expected an object")
    return value


def _array(value: object, path: str) -> list:
    if not isinstance(value, list):
        raise ValidationError(f"{path}: expected an array")
    return value


def _name(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{path}: expected a nonempty string")
    return value


def _event_name(value: object, path: str) -> str:
    name = _name(value, path).strip()
    if len(name) > 64:
        raise ValidationError(f"{path}: event name must be at most 64 characters")
    return name


def _integer(value: object, lower: int, upper: int, path: str) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValidationError(f"{path}: expected an integer from {lower} to {upper}")
    return value


def _require_type(expression, variables, expected, path):
    if _expression(expression, variables, path) is not expected:
        raise ValidationError(f"{path}: incompatible expression type")


def _steps(raw, workflows, variables, path, depth=0, return_type=None):
    if depth > 32:
        raise ValidationError(f"{path}: block nesting exceeds 32")
    result = []
    for index, item in enumerate(_array(raw, path)):
        location = f"{path}[{index}]"
        step = dict(_object(item, location))
        if "block_id" in step:
            block_id = _name(step["block_id"], f"{location}.block_id")
            location = f"{location} ({block_id})"
        op = step.get("op")
        if op == "wait":
            _integer(step.get("duration_ns"), 0, MAX_DURATION_NS, location)
        elif op == "emit_event":
            step["name"] = _event_name(step.get("name"), location)
        elif op == "mouse_click":
            if step.get("button") not in ("left", "right", "middle"):
                raise ValidationError(f"{location}: invalid mouse button")
            _integer(step.get("hold_ns"), 1, MAX_DURATION_NS, location)
        elif op in ("key_tap", "tap_sequence"):
            step["key"] = _key(step.get("key"), location)
            hold = _integer(step.get("hold_ns"), 1, MAX_DURATION_NS, location)
            if op == "tap_sequence":
                hz = _integer(step.get("frequency_hz"), 1, 200, location)
                _integer(step.get("count"), 1, MAX_REPEAT_COUNT, location)
                if hold * hz >= 1_000_000_000:
                    raise ValidationError(f"{location}: hold must be shorter than the period")
                if step.get("missed_tick_policy", "skip_missed") != "skip_missed":
                    raise ValidationError(f"{location}: unsupported missed tick policy")
        elif op in ("repeat", "forever"):
            if op == "repeat":
                count = step.get("count")
                if isinstance(count, dict):
                    _require_type(count, variables, int, location)
                else:
                    _integer(count, 0, MAX_REPEAT_COUNT, location)
            step["steps"] = _steps(step.get("steps"), workflows, variables, location, depth + 1, return_type)
        elif op == "if":
            _require_type(step.get("condition"), variables, bool, location)
            for branch in ("then", "else"):
                raw_branch = step.get("else", []) if branch == "else" else step.get("then")
                step[branch] = _steps(raw_branch, workflows, variables, location, depth + 1, return_type)
        elif op == "set":
            name = _name(step.get("var"), location)
            if name not in variables or name.startswith(PARAM_PREFIX):
                raise ValidationError(f"{location}: unknown variable {name!r}")
            _require_type(step.get("value"), variables, variables[name], location)
        elif op == "call":
            name = _name(step.get("workflow_id"), location)
            if name not in workflows:
                raise ValidationError(f"{location}: unknown workflow {name!r}")
            target = workflows[name]
            args = _array(step.get("args", []), location)
            if len(args) != len(target["params"]):
                raise ValidationError(f"{location}: incorrect workflow argument count")
            for arg, param in zip(args, target["params"]):
                _require_type(arg, variables, VALUE_TYPES[param["type"]], location)
            result_type = VALUE_TYPES.get(target.get("return_type"))
            if "result" in step:
                destination = _name(step["result"], location)
                if (destination.startswith(PARAM_PREFIX) or result_type is None
                        or variables.get(destination) is not result_type):
                    raise ValidationError(f"{location}: return value requires a matching global variable")
            if "then" in step or "else" in step:
                if result_type is not bool or "result" in step:
                    raise ValidationError(f"{location}: branching requires a boolean return value")
                for branch in ("then", "else"):
                    step[branch] = _steps(step.get(branch, []), workflows, variables, location,
                                          depth + 1, return_type)
        elif op == "return":
            if return_type is None:
                raise ValidationError(f"{location}: this workflow has no return type")
            _require_type(step.get("value"), variables, VALUE_TYPES[return_type], location)
        elif op != "end":
            raise ValidationError(f"{location}: unsupported opcode {op!r}")
        result.append(step)
    return result


def validate_program(data: object) -> dict:
    raw = _object(data, "program")
    _integer(raw.get("ir_version"), 1, 1, "ir_version")
    project_id = _name(raw.get("project_id"), "project_id")
    initial = _object(raw.get("globals", {}), "globals")
    variables = {
        _name(name, "global name"): _scalar_type(value, f"global {name}")
        for name, value in initial.items()
    }
    if any(name.startswith(PARAM_PREFIX) for name in variables):
        raise ValidationError("global variable uses a reserved parameter prefix")
    definitions = _object(raw.get("workflows"), "workflows")
    workflows = {}
    for name, definition in definitions.items():
        path = f"workflow {_name(name, 'workflow name')}"
        definition = _object(definition, path)
        params = []
        seen = set()
        for item in _array(definition.get("params", []), path):
            param = _object(item, path)
            parameter = _name(param.get("name"), path)
            kind = param.get("type")
            if parameter in seen or not isinstance(kind, str) or kind not in VALUE_TYPES:
                raise ValidationError(f"{path} ({param.get('block_id', '')}): invalid or duplicate parameter")
            seen.add(parameter)
            params.append({"name": parameter, "type": kind})
        returns = definition.get("return_type")
        if returns is not None and (not isinstance(returns, str) or returns not in VALUE_TYPES):
            raise ValidationError(f"{path}: invalid return type")
        workflows[name] = {"params": params, "return_type": returns}
    for name, workflow in workflows.items():
        scope = {**variables, **{PARAM_PREFIX + param["name"]: VALUE_TYPES[param["type"]]
                                 for param in workflow["params"]}}
        workflow["steps"] = _steps(definitions[name].get("steps"), workflows, scope,
                                   f"workflow {name}", return_type=workflow["return_type"])

    handlers = []
    ids = set()
    hotkeys = set()
    for index, item in enumerate(_array(raw.get("handlers"), "handlers")):
        path = f"handlers[{index}]"
        handler = dict(_object(item, path))
        identifier = _name(handler.get("id"), path)
        if identifier in ids:
            raise ValidationError(f"{path}: duplicate handler {identifier!r}")
        ids.add(identifier)
        workflow = _name(handler.get("workflow_id"), path)
        if workflow not in workflows:
            raise ValidationError(f"{path}: unknown workflow {workflow!r}")
        if workflows[workflow]["params"] or workflows[workflow]["return_type"] is not None:
            raise ValidationError(f"{path}: event workflow must have no parameters or return value")
        event = _object(handler.get("event"), path)
        event_type = event.get("type")
        if event_type == "hotkey_pressed":
            hotkey = normalize_hotkey(event.get("hotkey"))
            if hotkey in hotkeys or hotkey == DEFAULT_STOP_HOTKEY:
                raise ValidationError(f"{path}: duplicate or reserved hotkey {hotkey}")
            hotkeys.add(hotkey)
            handler["event"] = {"type": event_type, "hotkey": hotkey}
        elif event_type == "project_start":
            handler["event"] = {"type": event_type}
        elif event_type == "interval":
            period = _integer(event.get("period_ns"), 1_000_000, MAX_DURATION_NS, path)
            handler["event"] = {"type": event_type, "period_ns": period}
        elif event_type == "custom":
            handler["event"] = {"type": event_type, "name": _event_name(event.get("name"), path)}
        else:
            raise ValidationError(f"{path}: unsupported event {event_type!r}")
        handler.setdefault("policy", "ignore_if_running")
        if handler["policy"] not in ("ignore_if_running", "restart", "queue", "parallel", "override"):
            raise ValidationError(f"{path}: unsupported reentrancy policy")
        handler["priority"] = _integer(handler.get("priority", 0), -(2**31), 2**31 - 1, path)
        handler.setdefault("enabled", True)
        if type(handler["enabled"]) is not bool:
            raise ValidationError(f"{path}: enabled must be boolean")
        if "guard" in handler:
            _require_type(handler["guard"], variables, bool, path)
        handlers.append(handler)
    result = {
        "ir_version": 1, "project_id": project_id, "globals": dict(initial),
        "workflows": workflows, "handlers": handlers,
    }
    if "state_machine" in raw:
        result["state_machine"] = _state_machine(raw["state_machine"], workflows, variables)
    return result


def _state_machine(data, workflows, variables):
    raw = _object(data, "state_machine")
    states = _object(raw.get("states"), "state_machine.states")
    initial = raw.get("initial")
    if not isinstance(initial, str) or initial not in states:
        raise ValidationError("상태 머신에 시작 상태를 하나 지정하세요.")
    for name, state in states.items():
        _name(name, "state name")
        state = _object(state, f"state {name}")
        path = f"state {name} ({state.get('block_id', '')})"
        workflow = state.get("workflow_id", "")
        if not isinstance(workflow, str) or (workflow and workflow not in workflows):
            raise ValidationError(f"{path}: unknown entry workflow {workflow!r}")
        if workflow and (workflows[workflow]["params"] or workflows[workflow]["return_type"] is not None):
            raise ValidationError(f"{path}: state entry workflow must have no parameters or return value")
    transitions = []
    for item in _array(raw.get("transitions"), "state_machine.transitions"):
        transition = dict(_object(item, "transition"))
        path = f"transition ({transition.get('block_id', '')})"
        source, target = transition.get("from"), transition.get("to")
        if any(not isinstance(name, str) or name not in states for name in (source, target)):
            raise ValidationError(f"{path}: transition references an unknown state")
        if source == target:
            raise ValidationError(f"{path}: source and destination must differ")
        if "hotkey" in transition:
            try:
                transition["hotkey"] = normalize_hotkey(transition["hotkey"])
            except ValidationError as error:
                raise ValidationError(f"{path}: {error}") from error
            if transition["hotkey"] == DEFAULT_STOP_HOTKEY:
                raise ValidationError(f"{path}: reserved hotkey")
        elif "guard" not in transition:
            raise ValidationError(f"{path}: connect a transition condition")
        if "guard" in transition:
            _require_type(transition["guard"], variables, bool, path)
        transitions.append(transition)
    return {"initial": initial, "states": states, "transitions": transitions}
