"""Convert the common IR subset without discarding execution behavior."""

from copy import deepcopy
from dataclasses import dataclass, replace
import json
import keyword
import unicodedata

from .compiler import compile_project
from .ir import DURATION_UNIT_NS, LOCAL_PREFIX, PARAM_PREFIX, ValidationError, _SCAN_CODES, duration_nanoseconds
from .python_compiler import compile_python
from .python_dsl import AnalysisError, DECORATORS, NAMESPACES, SourceSpan


INFIX = {
    "add": "+", "sub": "-", "mul": "*", "div": "/", "eq": "==", "ne": "!=",
    "lt": "<", "le": "<=", "gt": ">", "ge": ">=", "and": "and", "or": "or",
}
KEYS = {f"scan:{value:02X}": name for name, value in _SCAN_CODES.items()}
EVENTS = {"project_start": "start", "hotkey_pressed": "hotkey", "interval": "interval"}
TYPES = {"number": "int", "string": "str", "boolean": "bool"}
MILLISECOND = DURATION_UNIT_NS["ms"]


class ConversionError(ValueError):
    def __init__(self, message, block_id=None, source=None):
        super().__init__(message)
        self.block_id = block_id
        self.source = source


def _fail(node, message):
    raise ConversionError(message, node.get("block_id", node.get("id")))


def _identifier(name):
    return (name.isidentifier() and not name.startswith("_") and not keyword.iskeyword(name)
            and unicodedata.normalize("NFKC", name) == name and name not in NAMESPACES
            and name not in DECORATORS and name != "range")


def _named_key(step):
    name = KEYS.get(step["key"])
    if name is None:
        _fail(step, "이 스캔 코드에 대응하는 DSL 논리 키가 없습니다.")
    return name


def _ms(step, *, hold=False):
    if "duration" in step:
        value, unit = step["duration"]["value"], step["duration"]["unit"]
        if unit == "ms" and (type(value) is int or isinstance(value, dict)):
            return value
        if isinstance(value, dict):
            _fail(step, "동적 시간은 ms 단위로 지정해야 왕복할 수 있습니다.")
        ns = duration_nanoseconds(value, unit)
    else:
        ns = step["hold_ns" if hold else "duration_ns"]
    milliseconds, remainder = divmod(ns, MILLISECOND)
    if remainder:
        _fail(step, "밀리초보다 작은 시간은 현재 DSL에서 표현할 수 없습니다.")
    return milliseconds


def _check_program(program):
    if "state_machine" in program:
        _fail({}, "상태 머신은 현재 왕복 변환 범위 밖입니다.")
    targets = {}
    for handler in program["handlers"]:
        name = handler["workflow_id"]
        if (name in targets or handler["event"]["type"] not in EVENTS
                or "guard" in handler or handler.get("enabled") is False):
            _fail(handler, "공유 핸들러, 이벤트 유형, 가드 또는 비활성 상태를 보존할 수 없습니다.")
        targets[name] = handler
    for name, workflow in program["workflows"].items():
        if workflow.get("return_type") or (name in targets and workflow.get("params")):
            _fail(targets.get(name, {}), f"워크플로 {name}의 타입 있는 반환이나 이벤트 인자를 변환할 수 없습니다.")
    return targets


def _walk(steps):
    for step in steps:
        yield step
        for field in ("steps", "then", "else"):
            yield from _walk(step.get(field, ()))


def _vision_kwargs(step):
    spec = step["spec"]
    if (spec["detector"] != "template" or step.get("on_error", "return_unknown") != "return_unknown"
            or step.get("timeout_ms") is None):
        _fail(step, "유한 시간의 템플릿 탐색과 대기만 왕복할 수 있습니다.")
    fields = {key: spec[key] for key in ("enter_threshold", "exit_threshold", "max_age_ms") if key in spec}
    fields.update({f"roi_{key}": value for key, value in (spec.get("roi") or {}).items()})
    return fields


def _check_step(step, targets):
    op = step["op"]
    if op == "call" and (step["workflow_id"] in targets or any(key in step for key in ("then", "else", "result"))):
        _fail(step, "이벤트 워크플로 직접 호출 또는 워크플로 반환값은 현재 변환할 수 없습니다.")
    if op == "mouse_move_timed" and (step["target"]["kind"] != "absolute" or "seed_override" in step["motion"]):
        _fail(step, "상대 좌표 이동 또는 별도 시드는 현재 변환할 수 없습니다.")
    if op in ("mouse_click", "mouse_click_at") and not step.get("revalidate_before_click", True):
        _fail(step, "클릭 재검사 해제를 현재 DSL에서 표현할 수 없습니다.")
    if op == "mouse_click_at" and _ms(step, hold=True) != 50:
        _fail(step, "좌표 클릭은 50ms 홀드만 왕복할 수 있습니다.")


@dataclass(frozen=True)
class CanonicalPython:
    source: str
    block_ids_by_line: tuple[tuple[int, str], ...]

    def compile(self, project, **settings):
        snapshot = compile_python(self.source, project, **settings)
        raw = json.loads(snapshot._raw_ir)
        spans = dict(snapshot.source_map)
        original = dict(self.block_ids_by_line)
        ids = {}
        for workflow in raw["workflows"].values():
            for step in _walk(workflow["steps"]):
                identifier = step["block_id"]
                if spans[identifier].line in original:
                    ids[identifier] = step["block_id"] = original[spans[identifier].line]
        for handler in raw["handlers"]:
            identifier = handler["id"]
            if spans[identifier].line in original:
                ids[identifier] = handler["id"] = original[spans[identifier].line]
        return replace(snapshot, _raw_ir=json.dumps(raw),
                       source_map=tuple((ids.get(key, key), span) for key, span in snapshot.source_map))


class _Python:
    def __init__(self, targets):
        self.targets = targets
        self.lines = []
        self.ids = []
        self.names = {}
        self.results = {}

    def line(self, depth, text, identifier=None):
        self.lines.append("    " * depth + text)
        if identifier:
            self.ids.append((len(self.lines), identifier))

    def expr(self, value, owner):
        if not isinstance(value, dict):
            return repr(value)
        if "var" in value:
            name = value["var"]
            name = self.names.get(name, name)
            if not _identifier(name):
                _fail(owner, f"Python 식별자로 사용할 수 없는 변수입니다. {name}")
            return name
        op = value["op"]
        if op == "vision_result" and value["result_var"] in self.results:
            return f"{self.results[value['result_var']]}.{value['field']}"
        if op == "not":
            return f"(not {self.expr(value['args'][0], owner)})"
        if op in INFIX:
            left, right = (self.expr(arg, owner) for arg in value["args"])
            return f"({left} {INFIX[op]} {right})"
        _fail(owner, f"지원하지 않는 식입니다. {op}")

    def body(self, steps, depth):
        if not steps:
            self.line(depth, "pass")
        for step in steps:
            _check_step(step, self.targets)
            op = step["op"]
            expr = lambda value: self.expr(value, step)
            identifier = step.get("block_id")
            match op:
                case "wait":
                    text = f"await macro.sleep({expr(_ms(step))})"
                case "key_tap":
                    text = f"await keyboard.tap({_named_key(step)!r}, {expr(_ms(step, hold=True))})"
                case "tap_sequence":
                    text = (f"await keyboard.repeat({_named_key(step)!r}, {expr(step['frequency_hz'])}, "
                            f"{expr(_ms(step, hold=True))}, {expr(step['count'])})")
                case "mouse_click":
                    text = f"await mouse.click({step['button']!r}, {expr(_ms(step, hold=True))})"
                case "mouse_click_at":
                    position = step["position"]
                    text = f"await mouse.click_at({expr(position['x'])}, {expr(position['y'])}, {step['button']!r})"
                case "mouse_move_timed":
                    target, motion = step["target"], step["motion"]
                    args = f"{expr(target['x'])}, {expr(target['y'])}, profile_id={motion['profile_ref']!r}"
                    if motion.get("duration") is not None:
                        args += f", duration_ms={expr(_ms({'duration': motion['duration'], 'block_id': identifier}))}"
                    text = f"await mouse.move_to({args})"
                case "vision_find" | "vision_wait":
                    kwargs = _vision_kwargs(step)
                    args = [repr(step["spec"]["image"]), f"timeout_ms={step['timeout_ms']}",
                            f"profile_id={step.get('profile_ref', 'inherit')!r}"]
                    if op == "vision_wait":
                        args.append(f"condition={step['condition']!r}")
                    args.extend(f"{name}={value!r}" for name, value in kwargs.items())
                    method = "find" if op == "vision_find" else "wait_for"
                    text = f"{self.results[step['result_var']]} = await vision.{method}({', '.join(args)})"
                case "call":
                    args = [repr(step["workflow_id"]), *(expr(arg) for arg in step.get("args", ()))]
                    text = f"await workflow.call({', '.join(args)})"
                case "set":
                    text = f"{expr({'var': step['var']})} = {expr(step['value'])}"
                case "repeat":
                    self.line(depth, f"for _ in range({expr(step['count'])}):", identifier)
                    self.body(step["steps"], depth + 1)
                    continue
                case "if":
                    self.line(depth, f"if {expr(step['condition'])}:", identifier)
                    self.body(step["then"], depth + 1)
                    if step.get("else"):
                        self.line(depth, "else:")
                        self.body(step["else"], depth + 1)
                    continue
                case "break" | "continue":
                    text = op
                case "end":
                    text = "await macro.stop()"
                case _:
                    _fail(step, f"지원하지 않는 명령입니다. {op}")
            self.line(depth, text, identifier)

    def workflow(self, function, decorator, workflow, globals_, identifier):
        params = workflow.get("params", ())
        self.names = {PARAM_PREFIX + item["name"]: item["name"] for item in params}
        taken = set(globals_) | set(self.names.values())
        self.results = {}

        def alias(prefix):
            index = 1
            while (name := f"{prefix}_{index}") in taken:
                index += 1
            taken.add(name)
            return name

        for variable in sorted(workflow.get("locals", {})):
            name = variable.removeprefix(LOCAL_PREFIX)
            if not _identifier(name) or name in taken:
                name = alias("local")
            taken.add(name)
            self.names[variable] = name
        for step in _walk(workflow["steps"]):
            if step["op"] in ("vision_find", "vision_wait") and step["result_var"] not in self.results:
                self.results[step["result_var"]] = alias("recognition")
        if any(not _identifier(item["name"]) for item in params):
            _fail({}, "Python에서 표현할 수 없는 워크플로 매개변수 이름입니다.")
        self.line(0, decorator)
        signature = ", ".join(f"{item['name']}: {TYPES[item['type']]}" for item in params)
        self.line(0, f"async def {function}({signature}):", identifier)
        used = _global_refs(workflow["steps"], globals_)
        if used:
            if any(not _identifier(name) for name in used):
                _fail({}, "Python에서 표현할 수 없는 전역 변수 이름입니다.")
            self.line(1, "global " + ", ".join(sorted(used)))
        self.body(workflow["steps"], 1)
        self.line(0, "")


def _global_refs(value, globals_):
    if isinstance(value, dict):
        refs = {item for field, item in value.items()
                if field == "var" and isinstance(item, str) and item in globals_}
        for item in value.values():
            refs.update(_global_refs(item, globals_))
        return refs
    if isinstance(value, list):
        return set().union(*(_global_refs(item, globals_) for item in value))
    return set()


def generate_python(program, project, **settings):
    targets = _check_program(program)
    writer = _Python(targets)
    globals_ = program.get("globals", {})
    for index, handler in enumerate(program["handlers"], 1):
        event = handler["event"]
        args = []
        if event["type"] == "hotkey_pressed":
            args.append(repr(event["hotkey"]))
        if event["type"] == "interval":
            args.append(f"ms={_ms({'duration_ns': event['period_ns'], 'block_id': handler['id']})}")
        args.extend((f"reentrancy={handler['policy']!r}", f"priority={handler['priority']}"))
        decorator = f"@on_{EVENTS[event['type']]}({', '.join(args)})"
        function = f"macro_event_{index}"
        while function in program["workflows"]:
            function += "_"
        writer.workflow(function, decorator, program["workflows"][handler["workflow_id"]], globals_, handler["id"])
    for index, (name, workflow) in enumerate(program["workflows"].items(), 1):
        if name not in targets:
            function = f"macro_workflow_{index}"
            while function in program["workflows"]:
                function += "_"
            writer.workflow(function, f"@workflow({name!r})", workflow, globals_, None)
    artifact = CanonicalPython("\n".join(writer.lines), tuple(writer.ids))
    try:
        rebuilt = artifact.compile(project, **settings).program_for(artifact.source, project, **settings)
    except AnalysisError as error:
        line = error.diagnostic.source.line
        raise ConversionError(str(error), dict(artifact.block_ids_by_line).get(line), error.diagnostic.source) from error
    _verify_equivalence(program, rebuilt)
    return artifact


class _Blockly:
    def __init__(self, program, targets):
        self.targets = targets
        self.count = 0
        self.locals = {}
        self.used_ids = set()

        def collect(value):
            if isinstance(value, dict):
                for field, item in value.items():
                    if field in ("id", "block_id") and isinstance(item, str):
                        self.used_ids.add(item)
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(program)
        self.params = {}
        for name, workflow in program["workflows"].items():
            self.params[name] = [dict(item, id=self.block_id()) for item in workflow.get("params", ())]

    def block_id(self):
        while True:
            self.count += 1
            candidate = f"converted-{self.count}"
            if candidate not in self.used_ids:
                self.used_ids.add(candidate)
                return candidate

    def block(self, kind, fields=None, inputs=None, *, identifier=None, extra=None):
        block = {"type": f"ms_{kind}", "id": identifier or self.block_id()}
        if fields:
            block["fields"] = fields
        if inputs:
            block["inputs"] = {name: {"block": value} for name, value in inputs.items() if value is not None}
        if extra:
            block["extraState"] = extra
        return block

    def expr(self, value, owner):
        if type(value) is bool:
            return self.block("boolean", {"VALUE": "TRUE" if value else "FALSE"})
        if type(value) is str:
            return self.block("text", {"VALUE": value})
        if type(value) in (int, float):
            if type(value) is int and abs(value) > 2**53 - 1:
                _fail(owner, "정수를 Blockly 숫자에 정확히 저장할 수 없습니다.")
            return self.block("number", {"VALUE": value})
        if "var" in value:
            name = value["var"]
            if name in self.locals:
                return self.block("local", {"NAME": name.removeprefix(LOCAL_PREFIX)})
            if name.startswith(PARAM_PREFIX):
                return self.block("parameter", {"NAME": name.removeprefix(PARAM_PREFIX)})
            return self.block("variable", {"NAME": name})
        op = value["op"]
        if op == "vision_result":
            return self.block(op, {"FIELD": value["field"], "RESULT": value["result_var"]},
                              identifier=value.get("block_id"))
        if op == "not":
            return self.block(op, inputs={"VALUE": self.expr(value["args"][0], owner)})
        if op in INFIX:
            left, right = (self.expr(arg, owner) for arg in value["args"])
            return self.block("binary", {"OP": op}, {"A": left, "B": right})
        _fail(owner, f"지원하지 않는 식입니다. {op}")

    def step(self, step):
        _check_step(step, self.targets)
        op = step["op"]
        block = lambda kind, fields=None, inputs=None, **extra: self.block(
            kind, fields, inputs, identifier=step.get("block_id"), **extra)
        expr = lambda value: self.expr(value, step)

        def duration(owner, hold=False):
            time = owner.get("duration")
            value, unit = (time["value"], time["unit"]) if time else (_ms(owner, hold=hold), "ms")
            return self.block("duration", {"UNIT": unit}, {"VALUE": expr(value)})

        match op:
            case "wait":
                return block("wait_time", inputs={"DURATION": duration(step)})
            case "key_tap" | "tap_sequence":
                inputs = {"KEY": self.block("key", {"VALUE": _named_key(step)}), "DURATION": duration(step, True)}
                if op == "tap_sequence":
                    inputs.update(HZ=expr(step["frequency_hz"]), COUNT=expr(step["count"]))
                return block(op, inputs=inputs)
            case "mouse_click":
                return block(op, {"BUTTON": step["button"]}, {"DURATION": duration(step, True)})
            case "mouse_click_at":
                position = step["position"]
                return block(op, {"BUTTON": step["button"]}, {"POSITION": self.block(
                    "position", inputs={"X": expr(position["x"]), "Y": expr(position["y"])})})
            case "mouse_move_timed":
                target, motion = step["target"], step["motion"]
                position = self.block("position", inputs={"X": expr(target["x"]), "Y": expr(target["y"])})
                time = duration(motion) if motion.get("duration") is not None else None
                return block(op, {"PROFILE": motion["profile_ref"]}, {"POSITION": position, "DURATION": time})
            case "vision_find" | "vision_wait":
                overrides = _vision_kwargs(step)
                fields = {"IMAGE": step["spec"]["image"], "PROFILE": step.get("profile_ref", "inherit"),
                          "RESULT": step["result_var"], "TIMEOUT_MS": step["timeout_ms"], "DETECTOR": "template"}
                fields.update({key.upper(): value if key.startswith("roi_") else str(value)
                               for key, value in overrides.items()})
                if op == "vision_wait":
                    fields["CONDITION"] = step["condition"]
                return block(op, fields)
            case "call":
                params = self.params[step["workflow_id"]]
                inputs = {"ARG_" + param["id"]: expr(arg) for param, arg in zip(params, step.get("args", ()))}
                return block(op, {"NAME": step["workflow_id"]}, inputs, extra={"parameters": params})
            case "set":
                name = step["var"]
                fields = {"NAME": name}
                if name in self.locals:
                    op = "local_set"
                    fields = {"NAME": name.removeprefix(LOCAL_PREFIX), "TYPE": self.locals[name]}
                return block(op, fields, {"VALUE": expr(step["value"])})
            case "repeat":
                return block(op, inputs={"BODY": _chain(self.step(item) for item in step["steps"]),
                                         "COUNT": expr(step["count"])})
            case "if":
                return block(op, inputs={"CONDITION": expr(step["condition"]),
                                         "THEN": _chain(self.step(item) for item in step["then"]),
                                         "ELSE": _chain(self.step(item) for item in step.get("else", ()))})
            case "break" | "continue" | "end":
                return block(op)
            case _:
                _fail(step, f"지원하지 않는 명령입니다. {op}")

    def workflow(self, name, workflow, handler=None):
        self.locals = workflow.get("locals", {})
        body = _chain(self.step(item) for item in workflow["steps"])
        if handler is None:
            params = _chain(self.block("param_def", {"NAME": p["name"], "TYPE": p["type"]}, identifier=p["id"])
                            for p in self.params[name])
            return self.block("workflow", {"NAME": name, "RETURNS": "void"}, {"BODY": body, "PARAMS": params})
        event = handler["event"]
        fields = {"POLICY": handler["policy"], "PRIORITY": handler["priority"]}
        inputs = {}
        if event["type"] == "hotkey_pressed":
            inputs["KEY"] = self.block("key", {"VALUE": event["hotkey"]})
        if event["type"] == "interval":
            fields["MS"] = _ms({"duration_ns": event["period_ns"], "block_id": handler["id"]})
        block = self.block(EVENTS[event["type"]], fields, inputs, identifier=handler["id"])
        if body is not None:
            block["next"] = {"block": body}
        return block


def _chain(blocks):
    first = last = None
    for block in blocks:
        if last is None:
            first = block
        else:
            if last["type"] in ("ms_break", "ms_continue", "ms_end"):
                _fail({"block_id": last["id"]}, "종료 블록 뒤의 명령은 Blockly에 연결할 수 없습니다.")
            last["next"] = {"block": block}
        last = block
    return first


def render_blocks(program, project, **settings):
    targets = _check_program(program)
    writer = _Blockly(program, targets)
    blocks = [writer.workflow(handler["workflow_id"], program["workflows"][handler["workflow_id"]], handler)
              for handler in program["handlers"]]
    blocks.extend(writer.workflow(name, workflow) for name, workflow in program["workflows"].items()
                  if name not in targets)
    for index, block in enumerate(blocks):
        block.update(x=48 + index % 2 * 400, y=48 + index // 2 * 300)
    workspace = {"blocks": {"languageVersion": 0, "blocks": blocks}}
    try:
        rebuilt, detached = compile_project({**project, "workspace": workspace}, **settings)
    except ValidationError as error:
        raise ConversionError(str(error)) from error
    if detached:
        raise ConversionError("변환된 블록이 실행 워크플로에 연결되지 않았습니다.", detached[0])
    _verify_equivalence(program, rebuilt)
    return workspace


def semantic_ir(program):
    """Normalize editor identities, aliases and equivalent timing encodings."""
    result = deepcopy(program)
    owners = {handler["workflow_id"]: index for index, handler in enumerate(result["handlers"])}

    def clean(value, names):
        if type(value) is bool:
            return ("boolean", value)
        if isinstance(value, list):
            return [clean(item, names) for item in value]
        if not isinstance(value, dict):
            return value
        value = {key: item for key, item in value.items() if key != "block_id"}
        for key in ("var", "result_var"):
            name = value.get(key)
            if name in names:
                value[key] = names[name]
        op = value.get("op")
        if "duration" in value:
            timing = value["duration"]
            if isinstance(timing, dict) and not isinstance(timing["value"], dict):
                ns = duration_nanoseconds(timing["value"], timing["unit"])
                time_key = "hold_ns" if op in ("key_tap", "tap_sequence", "mouse_click", "mouse_click_at") else "duration_ns"
                value[time_key] = ns
                del value["duration"]
        if op == "if":
            value.setdefault("else", [])
        if op == "call":
            value.setdefault("args", [])
        if op == "tap_sequence":
            value.setdefault("missed_tick_policy", "skip_missed")
        if op in ("mouse_click", "mouse_click_at"):
            value.setdefault("revalidate_before_click", True)
        if op in ("vision_find", "vision_wait"):
            value.setdefault("on_error", "return_unknown")
        return {key: clean(item, names) for key, item in value.items()}

    workflows = {}
    for name, workflow in result["workflows"].items():
        names, locals_ = {}, {}
        declared = workflow.pop("locals", {})
        steps = list(_walk(workflow["steps"]))
        for step in steps:
            variable = step.get("var")
            if variable in declared and variable not in names:
                alias = f"@local:{len(locals_)}"
                names[variable] = alias
                locals_[alias] = declared[variable]
        results = {}
        for step in steps:
            variable = step.get("result_var")
            if step["op"] in ("vision_find", "vision_wait") and variable not in results:
                results[variable] = f"@result:{len(results)}"
        workflow = clean(workflow, names | results)
        if locals_:
            workflow["locals"] = locals_
        key = ("event", owners[name]) if name in owners else ("workflow", name)
        workflows[key] = workflow
    handlers = []
    for handler in result["handlers"]:
        handler.pop("id")
        handler["workflow_id"] = owners[handler["workflow_id"]]
        handlers.append(clean(handler, {}))
    globals_ = {name: clean(value, {}) for name, value in result.get("globals", {}).items()}
    return {**{key: value for key, value in result.items() if key != "ir_version"}, "globals": globals_,
            "workflows": workflows, "handlers": handlers}


def _verify_equivalence(original, rebuilt):
    expected, actual = semantic_ir(original), semantic_ir(rebuilt)
    if expected == actual:
        return
    for key, left in expected["workflows"].items():
        right = actual["workflows"].get(key)
        if right is not None and left != right:
            name = original["handlers"][key[1]]["workflow_id"] if key[0] == "event" else key[1]
            steps = original["workflows"][name]["steps"]
            for index, (a, b) in enumerate(zip(left["steps"], right["steps"])):
                if a != b and index < len(steps):
                    _fail(steps[index], "변환 후 명령이나 설정의 실행 의미가 달라집니다.")
    raise ConversionError("변환 후 실행 의미가 달라집니다.")
