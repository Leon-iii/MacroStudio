"""Lower checked DSL syntax to the existing execution IR."""

import ast
from dataclasses import dataclass
from hashlib import sha256
import json

from .backend import FakeInputBackend, VirtualClock
from .ir import (
    LOCAL_PREFIX, MAX_CALL_DEPTH, MAX_REPEAT_COUNT, PARAM_PREFIX, VALUE_TYPES, VISION_FIELDS,
    ValidationError, _expression, validate_program,
)
from .mouse_motion import BUILTIN_PROFILES, DEFAULT_PROFILE
from .mouse_motion.registry import project_motion_profiles
from .output import FAKE_CAPABILITIES, FAKE_PROFILE, OutputKind, OutputRoute, OutputRouter, RunConfig, UnsupportedCapability, validate_capabilities
from .python_services import ServiceError, ServiceRegistry
from .python_dsl import (
    API_REGISTRY, AnalysisError, Diagnostic, MAX_MS, MAX_STRING_CHARS, Parameter, SourceSpan,
    _bind, _call, _check_name, _declarations, _fail, _parse, _span,
)
from .runtime import Runtime
from .vision_profiles import DEFAULT_PROFILE as DEFAULT_VISION_PROFILE, resolve_profile


TYPE_NAMES = {kind: name for name, kind in VALUE_TYPES.items()}
OPERATORS = {
    ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div", ast.And: "and", ast.Or: "or",
    ast.Eq: "eq", ast.NotEq: "ne", ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge",
}
RESULT_FIELDS = {**{name: name for name in VISION_FIELDS}, "center_x": "x", "center_y": "y"}
VISION_OVERRIDES = ("enter_threshold", "exit_threshold", "max_age_ms")
VISION_PARAMETERS = (Parameter("template_id", str),
                     Parameter("timeout_ms", int, minimum=1, maximum=MAX_MS, optional=True),
                     Parameter("profile_id", str, "inherit"),
                     *(Parameter(name, float, optional=True) for name in VISION_OVERRIDES[:2]),
                     Parameter("max_age_ms", int, minimum=1, maximum=MAX_MS, optional=True),
                     *(Parameter("roi_" + edge, int, optional=True) for edge in ("left", "top", "width", "height")))


def _serialized(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _fingerprint(source, project, application_profile, user_profiles, application_vision_profile):
    data = [source, project, application_profile.to_dict(),
            {key: profile.to_dict() for key, profile in (user_profiles or {}).items()},
            application_vision_profile]
    return sha256(_serialized(data).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class ExecutionSnapshot:
    fingerprint: str
    source_map: tuple[tuple[str, SourceSpan], ...]
    _raw_ir: str

    def program_for(self, source, project, *, application_profile=DEFAULT_PROFILE, user_profiles=None,
                    application_vision_profile=DEFAULT_VISION_PROFILE):
        fingerprint = _fingerprint(source, project, application_profile, user_profiles, application_vision_profile)
        if fingerprint != self.fingerprint:
            raise ValidationError("STALE_SNAPSHOT: 현재 Python 소스 또는 프로젝트 설정을 다시 검증하세요.")
        return validate_program(json.loads(self._raw_ir))


class _Workflow:
    def __init__(self, declaration, targets, project, profiles, vision_profile, source_map, services):
        self.declaration = declaration
        self.targets = targets
        self.project = project
        self.profiles = profiles
        self.vision_profile = vision_profile
        self.source_map = source_map
        self.services = services
        self.index = 0
        self.locals = {}
        self.params = {PARAM_PREFIX + name: kind for name, kind in declaration.parameters}
        self.initial = {name: PARAM_PREFIX + name for name, _ in declaration.parameters}
        self.calls = {}
        self.version = 1

    def _id(self, node):
        self.index += 1
        identifier = f"py:{self.declaration.name}:{self.index}"
        self.source_map[identifier] = _span(node)
        return identifier

    def _step(self, node, op, **fields):
        return {"op": op, "block_id": self._id(node), **fields}

    def _type(self, value, node):
        try:
            globals_ = {name: int if type(value) is float else type(value)
                        for name, value in self.project.get("globals", {}).items()}
            return _expression(value, globals_ | self.params | self.locals, "Python expression")
        except ValidationError as error:
            _fail(node, "TYPE_ERROR", str(error))

    def _expr(self, node, names):
        match node:
            case ast.Constant(value=value) if type(value) in (int, float, str, bool):
                if type(value) is str and len(value) > MAX_STRING_CHARS:
                    _fail(node, "INVALID_ARGUMENT", "문자열 길이 제한을 초과했습니다.")
                if type(value) is int and value.bit_length() > 64:
                    _fail(node, "INVALID_ARGUMENT", "정수 리터럴은 최대 64비트입니다.")
                return value
            case ast.Name(id=name):
                if (names.get(name) in self.params or names.get(name) in self.locals
                        or (names.get(name) == name and name in self.project.get("globals", {}))):
                    return {"var": names[name]}
                _fail(node, "UNKNOWN_NAME", f"값이 지정되지 않은 스칼라 변수입니다. {name}")
            case ast.Attribute(value=ast.Name(id=name), attr=field):
                variable = names.get(name, "")
                if not variable.startswith("py-result:") or field not in RESULT_FIELDS:
                    _fail(node, "UNKNOWN_NAME", "등록된 인식 결과 필드만 읽을 수 있습니다.")
                return {"op": "vision_result", "block_id": self._id(node),
                        "result_var": variable, "field": RESULT_FIELDS[field]}
            case ast.BinOp(left=left, op=op, right=right) if type(op) in OPERATORS:
                return {"op": OPERATORS[type(op)], "args": [self._expr(left, names), self._expr(right, names)]}
            case ast.Compare(left=left, ops=[op], comparators=[right]) if type(op) in OPERATORS:
                return {"op": OPERATORS[type(op)], "args": [self._expr(left, names), self._expr(right, names)]}
            case ast.BoolOp(op=op, values=values):
                code = OPERATORS[type(op)]
                value = self._expr(values[-1], names)
                for part in reversed(values[:-1]):
                    value = {"op": code, "args": [self._expr(part, names), value]}
                return value
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return {"op": "not", "args": [self._expr(operand, names)]}
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                value = self._expr(operand, names)
                if type(value) in (int, float):
                    return -value
                return {"op": "sub", "args": [0, value]}
            case _:
                _fail(node, "UNSUPPORTED_SYNTAX", "지원하지 않는 표현식입니다.")

    def _argument(self, node, parameter, names):
        value = self._expr(node, names)
        kind = self._type(value, node)
        valid = type(value) in (int, float) if parameter.kind is float else (
            kind is parameter.kind and (parameter.kind is not int or type(value) is not float))
        if not valid:
            _fail(node, "TYPE_ERROR", f"{parameter.name}의 타입이 올바르지 않습니다.")
        if not isinstance(value, dict) and (
            (parameter.minimum is not None and value < parameter.minimum)
            or (parameter.maximum is not None and value > parameter.maximum)
            or (parameter.choices and value not in parameter.choices)
            or (parameter.kind is str and not value.strip())
        ):
            _fail(node, "INVALID_ARGUMENT", f"{parameter.name}의 값이 허용 범위를 벗어났습니다.")
        return value

    def _local(self, node, name, kind, names):
        if names.get(name) == name and name in self.project.get("globals", {}):
            if self._type({"var": name}, node) is not kind:
                _fail(node, "TYPE_ERROR", f"전역 변수의 타입과 일치하지 않습니다. {name}")
            return name
        key = LOCAL_PREFIX + name
        if names.get(name, key) != key or self.locals.get(key, kind) is not kind:
            _fail(node, "TYPE_ERROR", f"같은 이름을 다른 타입이나 범위에 사용할 수 없습니다. {name}")
        self.locals[key] = kind
        names[name] = key
        self.version = 4
        return key

    def _vision(self, node, symbol, name, names):
        parameters = VISION_PARAMETERS
        if symbol == "vision.wait_for":
            parameters = (parameters[0], Parameter("condition", str, choices=("appear", "disappear")), *parameters[1:])
        args = _bind(node, parameters, names, read_value=self._argument)
        image, profile = args["template_id"], args["profile_id"]
        if type(image) is not str or image not in self.project.get("image_assets", {}):
            _fail(node, "UNKNOWN_ASSET", "프로젝트에 등록된 템플릿 ID를 지정하세요.")
        if type(profile) is not str:
            _fail(node, "INVALID_ARGUMENT", "인식 프로파일에는 문자열 리터럴이 필요합니다.")
        overrides = {key: args[key] for key in VISION_OVERRIDES if key in args}
        roi = {key.removeprefix("roi_"): value for key, value in args.items() if key.startswith("roi_")}
        if roi:
            overrides["roi"] = roi
        try:
            spec = resolve_profile(self.project, profile, image, overrides, application_profile=self.vision_profile)
        except ValueError as error:
            _fail(node, "UNKNOWN_ASSET", str(error))
        opcode = "vision_find" if symbol == "vision.find" else "vision_wait"
        timeout = args.get("timeout_ms", 1000 if opcode == "vision_find" else 5000)
        condition = args.get("condition")
        if type(timeout) is not int or (opcode == "vision_wait" and type(condition) is not str):
            _fail(node, "INVALID_ARGUMENT", "타임아웃과 대기 조건에는 리터럴 값이 필요합니다.")
        result = f"py-result:{self.declaration.name}:{name}"
        if names.get(name, result) != result or LOCAL_PREFIX + name in self.locals:
            _fail(node, "TYPE_ERROR", f"인식 결과 이름이 스칼라 변수와 충돌합니다. {name}")
        names[name] = result
        self.version = max(self.version, 3)
        extra = {"condition": condition} if opcode == "vision_wait" else {}
        return self._step(node, opcode, result_var=result, template_id=image, profile_ref=profile,
                          spec={**spec, "detector": "template", "image": image, "template_id": image},
                          timeout_ms=timeout, **extra)

    def _action(self, node, names):
        if _symbol(node) == "workflow.call":
            if not node.args or not isinstance(node.args[0], ast.Constant) or type(node.args[0].value) is not str:
                _fail(node, "INVALID_ARGUMENT", "첫 인자로 워크플로 이름을 지정하세요.")
            target = node.args[0].value
            if target not in self.targets:
                _fail(node, "UNKNOWN_NAME", f"정의된 워크플로가 없습니다. {target}")
            call = ast.copy_location(ast.Call(func=node.func, args=node.args[1:], keywords=node.keywords), node)
            parameters = tuple(Parameter(name, kind) for name, kind in self.targets[target].parameters)
            arguments = _bind(call, parameters, names, read_value=self._argument)
            self.calls[target] = node
            return self._step(node, "call", workflow_id=target, args=list(arguments.values()))
        call = _call(node, names, read_value=self._argument, registry=self.services.apis)
        args = dict(call.arguments)
        if call.symbol == "actions.run":
            action_id = args["action_id"]
            if type(action_id) is not str:
                _fail(node, "INVALID_ARGUMENT", "액션 ID에는 문자열 리터럴이 필요합니다.")
            try:
                steps = self.services.action(action_id)
            except ServiceError as error:
                _fail(node, error.code, str(error))
            lowered = [self._step(node, step["op"], **{key: value for key, value in step.items() if key != "op"})
                       for step in steps]
            return self._step(node, "repeat", count=1, steps=lowered)
        opcode = API_REGISTRY[call.symbol].opcode
        if opcode == "wait":
            args = {"duration": {"value": args["ms"], "unit": "ms"}}
        elif opcode in ("key_tap", "tap_sequence", "mouse_click"):
            args["duration"] = {"value": args.pop("hold_ms"), "unit": "ms"}
        elif opcode == "mouse_click_at":
            args = {"position": {"x": args["x"], "y": args["y"]}, "button": args["button"],
                    "hold_ns": 50_000_000}
        elif opcode == "mouse_move_timed":
            ref = args["profile_id"]
            profile = self.profiles.get(ref)
            if profile is None:
                _fail(node, "UNKNOWN_ASSET", f"모션 프로파일이 없습니다. {ref}")
            duration = {"value": args["duration_ms"], "unit": "ms"} if "duration_ms" in args else None
            args = {"target": {"kind": "absolute", "x": args["x"], "y": args["y"]},
                    "motion": {"profile_ref": ref, "profile": profile.to_dict(), "duration": duration}}
            self.version = max(self.version, 2)
        return self._step(node, opcode, **args)

    def _condition(self, expression, names):
        value = self._expr(expression, names)
        if self._type(value, expression) is not bool:
            _fail(expression, "TYPE_ERROR", "조건에는 bool 값이 필요합니다.")
        return value

    def steps(self, body, names, *, in_loop=False):
        steps = []
        for node in body:
            match node:
                case ast.Pass():
                    continue
                case ast.Expr(value=ast.Await(value=ast.Call() as call)):
                    steps.append(self._action(call, names))
                case ast.Assign(targets=[ast.Name(id=name)], value=ast.Await(value=ast.Call() as call)):
                    _check_name(node, name)
                    symbol = _symbol(call)
                    if symbol not in ("vision.find", "vision.wait_for"):
                        _fail(call, "UNSUPPORTED_SYNTAX", "이 단계에서는 인식 호출의 결과를 변수에 저장할 수 있습니다.")
                    steps.append(self._vision(call, symbol, name, names))
                case ast.Assign(targets=[ast.Name(id=name)], value=expression):
                    _check_name(node, name)
                    value = self._expr(expression, names)
                    key = self._local(node, name, self._type(value, expression), names)
                    steps.append(self._step(node, "set", var=key, value=value))
                case ast.AugAssign(target=ast.Name(id=name) as target, op=op, value=expression) if type(op) in OPERATORS:
                    value = {"op": OPERATORS[type(op)], "args": [self._expr(target, names), self._expr(expression, names)]}
                    key = self._local(node, name, self._type(value, node), names)
                    steps.append(self._step(node, "set", var=key, value=value))
                case ast.If(test=test, body=yes, orelse=no):
                    condition = self._condition(test, names)
                    left, right = names.copy(), names.copy()
                    branch = self._step(node, "if", condition=condition, then=self.steps(yes, left, in_loop=in_loop),
                                        **{"else": self.steps(no, right, in_loop=in_loop)})
                    names.update({key: value for key, value in left.items() if right.get(key) == value})
                    steps.append(branch)
                case ast.For(target=ast.Name(id=name), iter=ast.Call(func=ast.Name(id="range"), args=[limit], keywords=[]),
                             body=loop, orelse=[]):
                    count = self._argument(limit, Parameter("count", int, minimum=0, maximum=MAX_REPEAT_COUNT), names)
                    loop_names = names.copy()
                    if name == "_":
                        steps.append(self._step(node, "repeat", count=count,
                                                steps=self.steps(loop, loop_names, in_loop=True)))
                        continue
                    _check_name(node, name)
                    key = self._local(node, name, int, loop_names)
                    counter = self._local(node, f"#{self.index}", int, {})
                    steps.append(self._step(node, "set", var=counter, value=-1))
                    increment = self._step(node, "set", var=counter, value={"op": "add", "args": [{"var": counter}, 1]})
                    bind = self._step(node, "set", var=key, value={"var": counter})
                    steps.append(self._step(node, "repeat", count=count,
                                            steps=[increment, bind, *self.steps(loop, loop_names, in_loop=True)]))
                case ast.While(test=ast.Constant(value=True)):
                    _fail(node, "UNBOUNDED_LOOP", "반복 횟수 또는 종료 조건을 지정하세요.")
                case ast.While(test=test, body=loop, orelse=[]):
                    condition = self._condition(test, names)
                    stop = self._step(node, "if", condition={"op": "not", "args": [condition]},
                                      then=[self._step(node, "break")])
                    steps.append(self._step(node, "repeat", count=MAX_REPEAT_COUNT,
                                            steps=[stop, *self.steps(loop, names.copy(), in_loop=True)]))
                case ast.Break() | ast.Continue() if in_loop:
                    steps.append(self._step(node, type(node).__name__.lower()))
                case _:
                    _fail(node, "UNSUPPORTED_SYNTAX", "지원하지 않는 Python 문장입니다.")
        return steps


def _symbol(node):
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return f"{node.func.value.id}.{node.func.attr}"
    return ""


def compile_python(source, project, *, application_profile=DEFAULT_PROFILE, user_profiles=None,
                   application_vision_profile=DEFAULT_VISION_PROFILE):
    module = _parse(source)
    declarations = _declarations(module, header_only=True)
    targets = {item.name: item for item in declarations if item.kind == "workflow"}
    source_map = {}
    workflows, handlers, graph = {}, [], {}
    version = 1
    try:
        services = ServiceRegistry(project)
        default, snapshots = project_motion_profiles(project)
        profiles = BUILTIN_PROFILES | (user_profiles or {}) | snapshots
        profiles["inherit"] = profiles[default] if default else application_profile
        for node, declaration in zip(module.body, declarations):
            compiler = _Workflow(declaration, targets, project, profiles, application_vision_profile, source_map, services)
            names = compiler.initial.copy()
            body = list(node.body)
            while body and isinstance(body[0], ast.Global):
                statement = body.pop(0)
                for name in statement.names:
                    _check_name(statement, name)
                    if name in names or name not in project.get("globals", {}):
                        _fail(statement, "UNKNOWN_NAME", f"고유한 프로젝트 전역 변수를 지정하세요. {name}")
                    names[name] = name
            steps = compiler.steps(body, names)
            version = max(version, compiler.version)
            workflows[declaration.name] = {
                "params": [{"name": name, "type": TYPE_NAMES[kind]} for name, kind in declaration.parameters],
                "steps": steps,
            }
            if compiler.locals:
                workflows[declaration.name]["locals"] = {name: TYPE_NAMES[kind] for name, kind in compiler.locals.items()}
            graph[declaration.name] = compiler.calls
            if declaration.kind == "workflow":
                continue
            options = dict(declaration.options)
            handler_id = f"py:{declaration.name}"
            source_map[handler_id] = declaration.source
            event = {"type": declaration.kind}
            if declaration.kind == "hotkey_pressed":
                event["hotkey"] = options["key"]
            if declaration.kind == "interval":
                event["period_ns"] = options["ms"] * 1_000_000
            handlers.append({"id": handler_id, "workflow_id": declaration.name, "event": event,
                             "priority": options["priority"], "policy": options["reentrancy"]})
        _check_calls(graph)
        raw = {"ir_version": version, "project_id": project.get("project_id"), "globals": project.get("globals", {}),
               "workflows": workflows, "handlers": handlers}
        validated = validate_program(raw)
        validate_capabilities(validated, services.capabilities)
        fingerprint = _fingerprint(source, project, application_profile, user_profiles, application_vision_profile)
    except AnalysisError:
        raise
    except ServiceError as error:
        raise AnalysisError(Diagnostic(error.code, str(error), SourceSpan(1, 0, 1, 0))) from None
    except UnsupportedCapability as error:
        span = next((span for block_id, span in source_map.items() if f"(block {block_id})" in str(error)), SourceSpan(1, 0, 1, 0))
        raise AnalysisError(Diagnostic("MISSING_CAPABILITY", str(error), span)) from None
    except (ValueError, RecursionError) as error:
        source_span = next((span for block_id, span in source_map.items() if f"({block_id})" in str(error)),
                           SourceSpan(1, 0, 1, 0))
        raise AnalysisError(Diagnostic("INVALID_IR", str(error), source_span)) from None
    return ExecutionSnapshot(fingerprint, tuple(source_map.items()), _serialized(raw))


def _check_calls(graph):
    depths = {}

    def depth(name, path):
        if name in depths:
            return depths[name]
        longest = 1
        for target, node in graph[name].items():
            if target in path or target == name:
                _fail(node, "RECURSIVE_WORKFLOW", "재귀 워크플로 호출을 허용하지 않습니다.")
            if len(path) + 1 >= MAX_CALL_DEPTH:
                _fail(node, "CALL_DEPTH", "워크플로 호출 깊이 제한을 초과했습니다.")
            longest = max(longest, 1 + depth(target, (*path, name)))
            if len(path) + longest > MAX_CALL_DEPTH:
                _fail(node, "CALL_DEPTH", "워크플로 호출 깊이 제한을 초과했습니다.")
        depths[name] = longest
        return longest

    for name in graph:
        depth(name, ())


def dry_run_python(snapshot, source, project, *, duration_ns, hotkeys=(), perception=None, clock=None,
                   poll=None, **compile_settings):
    program = snapshot.program_for(source, project, **compile_settings)
    clock = VirtualClock() if clock is None else clock
    output = FakeInputBackend(clock)
    session = OutputRouter({OutputKind.FAKE: OutputRoute(FAKE_CAPABILITIES, lambda: output)}).bind(
        program, RunConfig(FAKE_PROFILE),
    )
    runtime = Runtime(program, clock, session.backend, perception=perception)
    runtime.run(duration_ns, list(hotkeys), poll=poll)
    return runtime, output
