import math
import operator
from random import uniform
import re

from .mouse_motion import MouseMotionProfile

MAX_REPEAT_COUNT = 1_000_000
MAX_CALL_DEPTH = 32
MAX_DURATION_NS = 86_400_000_000_000
MAX_BATCH_EVENTS = 8
MAX_BATCH_US = 5_000_000
DURATION_UNIT_NS = {"ms": 1_000_000, "sec": 1_000_000_000, "min": 60_000_000_000}
DEFAULT_STOP_HOTKEY = "CTRL+ALT+SHIFT+F11"
SCREEN_SENSOR_OPS = ("pixel_color", "region_image", "region_text")
VISION_COMMANDS = ("vision_find", "vision_wait")
VISION_EVENTS = ("on_image_appeared", "on_image_disappeared")
VISION_FIELDS = {"x": int, "y": int, "score": int, "status": str, "error_code": str,
                 "pixel_count": int, "fraction": int}
SENSOR_COORDINATES = {"x": (-32768, 32767), "y": (-32768, 32767)}
SENSOR_REGION = {**SENSOR_COORDINATES, "width": (1, 1024), "height": (1, 1024)}
OCR_LANGUAGES = ("eng", "kor", "kor+eng")
VALUE_TYPES = {"number": int, "boolean": bool, "string": str}
PARAM_PREFIX = "@arg:"
LOCAL_PREFIX = "@local:"

_BINARY = {
    "add": operator.add, "sub": operator.sub, "mul": operator.mul, "div": operator.truediv,
    "random_float": uniform,
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
_SCAN_CODES.update(LEFT=0xE04B, UP=0xE048, RIGHT=0xE04D, DOWN=0xE050)
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


def sensor_geometry(spec):
    return SENSOR_COORDINATES if spec["op"] == "pixel_color" else SENSOR_REGION


def sensor_has_dynamic_geometry(spec):
    return any(type(spec[name]) is not int for name in sensor_geometry(spec))


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
    if op == "vision_visible":
        _vision_spec(value, path)
        return bool
    if op == "vision_result":
        location = f"{path} ({_name(value.get('block_id'), path)})"
        _name(value.get("result_var"), location)
        field = value.get("field")
        if field not in tuple(VISION_FIELDS):
            raise ValidationError(f"{location}: invalid recognition result field")
        return VISION_FIELDS[field]
    if op in SCREEN_SENSOR_OPS:
        label = f"{path} ({_name(value.get('block_id'), path)})"
        for name, (lower, upper) in sensor_geometry(value).items():
            operand = value.get(name)
            if _expression(operand, variables, f"{label}.{name}", depth + 1) is not int:
                raise ValidationError(f"{label}.{name}: expected a number")
            if not isinstance(operand, dict):
                _integer(round(operand), lower, upper, f"{label}.{name}")
        if op == "pixel_color":
            color = value.get("color")
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValidationError(f"{label}: expected #RRGGBB")
        else:
            if op == "region_image":
                _name(value.get("image"), label)
                threshold = value.get("min_similarity", 1.0)
                if type(threshold) not in (float, int) or not 0 <= threshold <= 1:
                    raise ValidationError(f"{label}: minimum image similarity must be between 0 and 1")
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


def evaluate(value, variables: dict, sensors=None, window=None, sensors_at=None, *, vision=None, on_variable=None):
    if not isinstance(value, dict):
        return value
    if "var" in value:
        if on_variable is not None:
            on_variable(value["var"])
        try:
            return variables[value["var"]]
        except KeyError:
            raise ValueError(f"variable {value['var']!r} has no value") from None
    op = value["op"]
    if op == "target_window":
        return window is not None and window(value["state"])
    if op in ("vision_visible", "vision_result"):
        if vision is None:
            raise ValueError("recognition expressions need a runtime")
        return vision(value)

    def operand(child):
        return evaluate(child, variables, sensors, window, sensors_at, vision=vision, on_variable=on_variable)

    if op in SCREEN_SENSOR_OPS:
        if not sensor_has_dynamic_geometry(value):
            return sensors is not None and sensors(value["block_id"])
        if sensors_at is None:
            return False
        bounds = []
        for name, (lower, upper) in sensor_geometry(value).items():
            number = round(operand(value[name]))
            if not lower <= number <= upper:
                return False
            bounds.append(number)
        return sensors_at(value["block_id"], tuple(bounds))
    args = value["args"]
    left = operand(args[0])
    if op == "not":
        return not left
    if op == "and":
        return left and operand(args[1])
    if op == "or":
        return left or operand(args[1])
    result = _BINARY[op](left, operand(args[1]))
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


def _vision_spec(node, path):
    location = f"{path} ({_name(node.get('block_id'), path)})"
    spec = _object(node.get("spec"), location)
    detector = spec.get("detector")
    if detector == "template":
        _name(spec.get("image"), f"{location}.image")
        if spec.get("method", "TM_CCOEFF_NORMED") != "TM_CCOEFF_NORMED":
            raise ValidationError(f"{location}: unsupported template matching method")
    elif detector != "hsv":
        raise ValidationError(f"{location}: unsupported detector {detector!r}")
    if "roi" in spec:
        roi = _object(spec["roi"], f"{location}.roi")
        if set(roi) != {"left", "top", "width", "height"}:
            raise ValidationError(f"{location}.roi: expected source-relative left, top, width and height")
        for edge in ("left", "top", "width", "height"):
            _integer(roi.get(edge), 1 if edge in ("width", "height") else 0, 32768, f"{location}.roi.{edge}")
    for name, lower, upper in (
        ("max_age_ms", 1, 86_400_000), ("scan_interval_ms", 1, 86_400_000),
        ("appearance_frames", 1, 1000), ("disappearance_frames", 1, 1000),
        ("cooldown_ms", 0, 86_400_000), ("min_pixel_count", 1, 32768**2),
    ):
        if name in spec:
            _integer(spec[name], lower, upper, f"{location}.{name}")
    for name, lower in (("enter_threshold", -1), ("exit_threshold", -1), ("min_fraction", 0)):
        value = spec.get(name, 0)
        if type(value) not in (int, float) or not lower <= value <= 1:
            raise ValidationError(f"{location}.{name}: expected a number from {lower} to 1")
    enter = spec.get("enter_threshold", 0.90)
    if spec.get("exit_threshold", min(0.82, enter)) > enter:
        raise ValidationError(f"{location}: exit threshold must not exceed enter threshold")
    if type(spec.get("fire_on_initial_present", False)) is not bool:
        raise ValidationError(f"{location}: fire_on_initial_present must be boolean")
    if detector == "hsv":
        for name in ("hsv_lower", "hsv_upper"):
            values = _array(spec.get(name), f"{location}.{name}")
            if len(values) != 3:
                raise ValidationError(f"{location}.{name}: expected three HSV channels")
            for value, limit in zip(values, (179, 255, 255)):
                _integer(value, 0, limit, f"{location}.{name}")
        if any(lo > hi for lo, hi in zip(spec["hsv_lower"][1:], spec["hsv_upper"][1:])):
            raise ValidationError(f"{location}: HSV saturation and value ranges must be ordered")


def vision_nodes(value):
    if isinstance(value, dict):
        if value.get("op") in (*VISION_COMMANDS, "vision_visible", "vision_result") or value.get("type") in VISION_EVENTS:
            yield value
        for child in value.values():
            yield from vision_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from vision_nodes(child)


def _require_type(expression, variables, expected, path):
    if _expression(expression, variables, path) is not expected:
        raise ValidationError(f"{path}: incompatible expression type")


def duration_nanoseconds(value, unit):
    scale = DURATION_UNIT_NS[unit]
    if not 0 <= value <= MAX_DURATION_NS // scale:
        raise ValueError("duration must be between 0 and 24 hours")
    return round(value * scale)


def _duration(raw, variables, path):
    duration = _object(raw, path)
    unit = duration.get("unit")
    if not isinstance(unit, str) or unit not in DURATION_UNIT_NS:
        raise ValidationError(f"{path}: expected duration unit ms, sec or min")
    value = duration.get("value")
    _require_type(value, variables, int, path)
    if isinstance(value, dict):
        return None
    try:
        return duration_nanoseconds(value, unit)
    except ValueError as error:
        raise ValidationError(f"{path}: {error}") from error


def _hold_duration(step, variables, path):
    if "duration" not in step:
        return _integer(step.get("hold_ns"), 1, MAX_DURATION_NS, path)
    if "hold_ns" in step:
        raise ValidationError(f"{path}: expected one hold duration")
    hold = _duration(step["duration"], variables, path)
    return None if hold is None else _integer(hold, 1, MAX_DURATION_NS, path)


def _integer_expression(value, variables, lower, upper, path):
    if isinstance(value, dict):
        _require_type(value, variables, int, path)
        return None
    if type(value) is float and value.is_integer():
        value = int(value)
    return _integer(value, lower, upper, path)


def _batch_events(raw, path):
    events = _array(raw, path)
    if not 1 <= len(events) <= MAX_BATCH_EVENTS:
        raise ValidationError(f"{path}: expected 1 to {MAX_BATCH_EVENTS} events")
    held = set()
    kinds = set()
    normalized = []
    previous = 0
    for index, item in enumerate(events):
        location = f"{path}[{index}]"
        item = _object(item, location)
        at = _integer(item.get("offset_us"), previous, MAX_BATCH_US, f"{location}.offset_us")
        previous = at
        action = item.get("action")
        edge = {"offset_us": at, "action": action}
        if action == "mouse_move":
            for axis in ("dx", "dy"):
                edge[axis] = _integer(item.get(axis), -127, 127, f"{location}.{axis}")
            kinds.add("move")
        elif action in ("key_down", "key_up", "mouse_down", "mouse_up"):
            if action.startswith("key"):
                edge["key"] = value = _key(item.get("key"), location)
                identifier = (False, value)
            else:
                value = item.get("button")
                if value not in ("left", "right", "middle"):
                    raise ValidationError(f"{location}: invalid mouse button")
                edge["button"] = value
                identifier = (True, value)
                kinds.add("click")
            if action.endswith("_down"):
                if identifier in held:
                    raise ValidationError(f"{location}: input is already held")
                held.add(identifier)
            elif identifier not in held:
                raise ValidationError(f"{location}: input was not pressed")
            else:
                held.remove(identifier)
        else:
            raise ValidationError(f"{location}: invalid timed batch action")
        normalized.append(edge)
    if held:
        raise ValidationError(f"{path}: every press must have a matching release")
    if kinds == {"click", "move"}:
        raise ValidationError(f"{path}: mouse movement and buttons must use separate batches")
    return normalized


def _steps(raw, workflows, variables, path, depth=0, return_type=None, *, in_loop=False, ir_version=1):
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
        if op in ("mouse_move", "mouse_click_at", "mouse_move_timed"):
            position = _object(step.get("target" if op == "mouse_move_timed" else "position"), location)
            for axis in ("x", "y"):
                coordinate = position.get(axis)
                axis_path = f"{location}.{axis}"
                if type(coordinate) is int:
                    _integer(coordinate, -32768, 32767, axis_path)
                else:
                    _require_type(coordinate, variables, int, axis_path)
        if op in VISION_COMMANDS:
            _vision_spec(step, location)
            _name(step.get("result_var"), location)
            timeout = step.get("timeout_ms", 1000 if op == "vision_find" else 5000)
            if timeout is not None or op == "vision_find":
                _integer(timeout, 1, 86_400_000, f"{location}.timeout_ms")
            step["timeout_ms"] = timeout
            if op == "vision_wait" and step.get("condition") not in ("appear", "disappear"):
                raise ValidationError(f"{location}: invalid vision wait condition")
            if step.get("on_error", "return_unknown") not in ("return_unknown", "stop_task"):
                raise ValidationError(f"{location}: invalid recognition error policy")
        elif op == "wait":
            if "duration" in step:
                if "duration_ns" in step:
                    raise ValidationError(f"{location}: expected one duration")
                _duration(step["duration"], variables, location)
            else:
                _integer(step.get("duration_ns"), 0, MAX_DURATION_NS, location)
        elif op == "mouse_move":
            _duration(step.get("duration"), variables, location)
        elif op == "mouse_move_timed":
            if ir_version < 2:
                raise ValidationError(f"{location}: profiled move requires IR version 2")
            if position.get("kind") not in ("absolute", "relative"):
                raise ValidationError(f"{location}: expected absolute or relative target")
            motion = dict(_object(step.get("motion"), location))
            try:
                profile = MouseMotionProfile.from_dict(motion.get("profile"))
            except ValueError as error:
                raise ValidationError(f"{location}: {error}") from error
            if motion.get("profile_ref") not in ("inherit", profile.id):
                raise ValidationError(f"{location}: motion profile reference does not match its snapshot")
            duration = motion.get("duration")
            if duration is not None:
                ns = _duration(duration, variables, location)
                if ns is not None and not 1_000_000 <= ns <= 60_000_000_000:
                    raise ValidationError(f"{location}: profiled duration must be between 1 and 60000 ms")
            seed = motion.get("seed_override")
            if seed is not None and type(seed) is not int:
                raise ValidationError(f"{location}: motion seed must be an integer")
            motion["profile"] = profile.to_dict()
            step["motion"] = motion
        elif op == "emit_event":
            step["name"] = _event_name(step.get("name"), location)
        elif op == "timed_batch":
            step["events"] = _batch_events(step.get("events"), f"{location}.events")
        elif op in ("mouse_click", "mouse_click_at"):
            if step.get("button") not in ("left", "right", "middle"):
                raise ValidationError(f"{location}: invalid mouse button")
            _hold_duration(step, variables, location)
            if type(step.get("revalidate_before_click", True)) is not bool:
                raise ValidationError(f"{location}: revalidate_before_click must be boolean")
        elif op in ("key_tap", "tap_sequence"):
            step["key"] = _key(step.get("key"), location)
            hold = _hold_duration(step, variables, location)
            if op == "tap_sequence":
                hz = _integer_expression(step.get("frequency_hz"), variables, 1, 200, f"{location}.frequency_hz")
                _integer_expression(step.get("count"), variables, 1, MAX_REPEAT_COUNT, f"{location}.count")
                if hold is not None and hz is not None and hold * hz >= 1_000_000_000:
                    raise ValidationError(f"{location}: hold must be shorter than the period")
                if step.get("missed_tick_policy", "skip_missed") != "skip_missed":
                    raise ValidationError(f"{location}: unsupported missed tick policy")
        elif op in ("repeat", "forever", "while"):
            if op == "repeat":
                count = step.get("count")
                if isinstance(count, dict):
                    _require_type(count, variables, int, location)
                else:
                    _integer(count, 0, MAX_REPEAT_COUNT, location)
            elif op == "while":
                _require_type(step.get("condition"), variables, bool, location)
            step["steps"] = _steps(step.get("steps"), workflows, variables, location,
                                   depth + 1, return_type, in_loop=True, ir_version=ir_version)
        elif op == "if":
            _require_type(step.get("condition"), variables, bool, location)
            for branch in ("then", "else"):
                raw_branch = step.get("else", []) if branch == "else" else step.get("then")
                step[branch] = _steps(raw_branch, workflows, variables, location,
                                      depth + 1, return_type, in_loop=in_loop, ir_version=ir_version)
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
                if (destination.startswith(PARAM_PREFIX) or (ir_version >= 4 and destination.startswith(LOCAL_PREFIX))
                        or result_type is None
                        or variables.get(destination) is not result_type):
                    raise ValidationError(f"{location}: return value requires a matching global variable")
            if "then" in step or "else" in step:
                if result_type is not bool or "result" in step:
                    raise ValidationError(f"{location}: branching requires a boolean return value")
                for branch in ("then", "else"):
                    step[branch] = _steps(step.get(branch, []), workflows, variables, location,
                                          depth + 1, return_type, in_loop=in_loop, ir_version=ir_version)
        elif op == "return":
            if return_type is None:
                raise ValidationError(f"{location}: this workflow has no return type")
            _require_type(step.get("value"), variables, VALUE_TYPES[return_type], location)
        elif op in ("break", "continue"):
            if not in_loop:
                raise ValidationError(f"{location}: {op} requires an enclosing loop")
        elif op != "end":
            raise ValidationError(f"{location}: unsupported opcode {op!r}")
        result.append(step)
    return result


def validate_program(data: object) -> dict:
    raw = _object(data, "program")
    version = _integer(raw.get("ir_version"), 1, 4, "ir_version")
    project_id = _name(raw.get("project_id"), "project_id")
    initial = _object(raw.get("globals", {}), "globals")
    variables = {
        _name(name, "global name"): _scalar_type(value, f"global {name}")
        for name, value in initial.items()
    }
    scope_prefixes = (PARAM_PREFIX, LOCAL_PREFIX) if version >= 4 else (PARAM_PREFIX,)
    if any(name.startswith(scope_prefixes) for name in variables):
        raise ValidationError("global variable uses a reserved scope prefix")
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
        if "locals" in definition:
            if version < 4:
                raise ValidationError(f"{path}: workflow locals require IR version 4")
            local = _object(definition["locals"], path)
            for key, kind in local.items():
                if (not isinstance(key, str) or not key.startswith(LOCAL_PREFIX) or not key[len(LOCAL_PREFIX):]
                        or not isinstance(kind, str) or kind not in VALUE_TYPES):
                    raise ValidationError(f"{path}: invalid local variable {key!r}")
            workflows[name]["locals"] = dict(local)
    for name, workflow in workflows.items():
        scope = {**variables, **{PARAM_PREFIX + param["name"]: VALUE_TYPES[param["type"]]
                                 for param in workflow["params"]}}
        scope.update({key: VALUE_TYPES[kind] for key, kind in workflow.get("locals", {}).items()})
        workflow["steps"] = _steps(definitions[name].get("steps"), workflows, scope,
                                   f"workflow {name}", return_type=workflow["return_type"], ir_version=version)

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
        elif event_type in VISION_EVENTS:
            event = {**event, "block_id": event.get("block_id", handler.get("block_id", identifier))}
            _vision_spec(event, path)
            handler["event"] = event
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
        "ir_version": version, "project_id": project_id, "globals": dict(initial),
        "workflows": workflows, "handlers": handlers,
    }
    if "state_machine" in raw:
        result["state_machine"] = _state_machine(raw["state_machine"], workflows, variables)
    if version < 3 and (node := next(vision_nodes(result), None)) is not None:
        raise ValidationError(f"({node.get('block_id')}) recognition requires IR version 3")
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
