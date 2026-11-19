"""Static analysis for the supported MacroStudio Python syntax."""

import ast
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .ir import DEFAULT_STOP_HOTKEY, MAX_DURATION_NS, MAX_REPEAT_COUNT, ValidationError, normalize_hotkey


MAX_SOURCE_CHARS = 64 * 1024
MAX_AST_NODES = 4096
MAX_STRING_CHARS = 1024
MAX_MS = MAX_DURATION_NS // 1_000_000
POLICIES = ("ignore_if_running", "restart", "queue", "parallel", "override")
Value = str | int | bool


@dataclass(frozen=True)
class SourceSpan:
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    source: SourceSpan


class AnalysisError(ValueError):
    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: type
    default: Value | None = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    optional: bool = False


@dataclass(frozen=True)
class Api:
    opcode: str
    parameters: tuple[Parameter, ...]
    required_capabilities: tuple[str, ...] = ()


API_REGISTRY = MappingProxyType({
    "macro.sleep": Api("wait", (Parameter("ms", int, minimum=0, maximum=MAX_MS),)),
    "macro.stop": Api("end", ()),
    "keyboard.tap": Api("key_tap", (Parameter("key", str), Parameter("hold_ms", int, 30, 1, MAX_MS)),
                        ("output.keyboard",)),
    "keyboard.repeat": Api("tap_sequence", (
        Parameter("key", str), Parameter("frequency_hz", int, minimum=1, maximum=200),
        Parameter("hold_ms", int, minimum=1, maximum=MAX_MS),
        Parameter("count", int, minimum=1, maximum=MAX_REPEAT_COUNT),
    ), ("output.keyboard",)),
    "mouse.click": Api("mouse_click", (
        Parameter("button", str, "left", choices=("left", "right", "middle")),
        Parameter("hold_ms", int, 50, 1, MAX_MS),
    ), ("output.mouse",)),
    "mouse.click_at": Api("mouse_click_at", (
        Parameter("x", int, minimum=-32768, maximum=32767),
        Parameter("y", int, minimum=-32768, maximum=32767),
        Parameter("button", str, "left", choices=("left", "right", "middle")),
    ), ("output.mouse",)),
    "mouse.move_to": Api("mouse_move_timed", (
        Parameter("x", int, minimum=-32768, maximum=32767),
        Parameter("y", int, minimum=-32768, maximum=32767),
        Parameter("profile_id", str, "inherit"),
        Parameter("duration_ms", int, minimum=1, maximum=60000, optional=True),
    ), ("output.mouse",)),
})
OPTIONAL_SERVICES = frozenset({"actions.run", "navigation.move_to", "spatial.observe"})
EVENT_OPTIONS = (Parameter("reentrancy", str, "ignore_if_running", choices=POLICIES),
                 Parameter("priority", int, 0, -(2**31), 2**31 - 1))
DECORATORS = {
    "on_start": EVENT_OPTIONS,
    "on_hotkey": (Parameter("key", str), *EVENT_OPTIONS),
    "on_interval": (Parameter("ms", int, minimum=1, maximum=MAX_MS), *EVENT_OPTIONS),
    "workflow": (Parameter("name", str),),
}
EVENT_TYPES = {"on_start": "project_start", "on_hotkey": "hotkey_pressed", "on_interval": "interval"}
NAMESPACES = frozenset(name.split(".")[0] for name in (*API_REGISTRY, *OPTIONAL_SERVICES, "vision.find", "workflow.call"))
WORKFLOW_TYPES = {"int": int, "str": str, "bool": bool}


@dataclass(frozen=True)
class Reference:
    name: str


@dataclass(frozen=True)
class CheckedCall:
    symbol: str
    arguments: tuple[tuple[str, Value | Reference], ...]
    source: SourceSpan


@dataclass(frozen=True)
class Declaration:
    name: str
    kind: Literal["project_start", "hotkey_pressed", "interval", "workflow"]
    options: tuple[tuple[str, Value], ...]
    parameters: tuple[tuple[str, type], ...]
    calls: tuple[CheckedCall, ...]
    source: SourceSpan


def _span(node: ast.AST) -> SourceSpan:
    return SourceSpan(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)


def _fail(node: ast.AST, code: str, message: str):
    raise AnalysisError(Diagnostic(code, message, _span(node)))


def _check_name(node: ast.AST, name: str):
    if name.startswith("_") or name in NAMESPACES or name in DECORATORS:
        _fail(node, "INVALID_NAME", f"예약된 이름입니다. {name}")


def _value(node, parameter, symbols):
    match node:
        case ast.Constant(value=value):
            pass
        case ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=number)) if type(number) is int:
            value = -number
        case ast.Name(id=name):
            if name not in symbols:
                _fail(node, "UNKNOWN_NAME", f"선언되지 않은 이름입니다. {name}")
            if symbols[name] is not parameter.kind:
                _fail(node, "TYPE_ERROR", f"{parameter.name}에는 {parameter.kind.__name__}이 필요합니다.")
            return Reference(name)
        case _:
            _fail(node, "UNSUPPORTED_SYNTAX", "인자에는 리터럴이나 선언된 매개변수를 사용하세요.")
    if type(value) is not parameter.kind:
        _fail(node, "TYPE_ERROR", f"{parameter.name}에는 {parameter.kind.__name__}이 필요합니다.")
    if ((type(value) is str and (not value.strip() or len(value) > MAX_STRING_CHARS))
            or (parameter.minimum is not None and value < parameter.minimum)
            or (parameter.maximum is not None and value > parameter.maximum)
            or (parameter.choices and value not in parameter.choices)):
        _fail(node, "INVALID_ARGUMENT", f"{parameter.name}의 값이 허용 범위를 벗어났습니다.")
    return value


def _bind(node, parameters, symbols, *, defaults=None, read_value=_value):
    defaults = defaults or {}
    positional = node.args if isinstance(node, ast.Call) else ()
    keywords = node.keywords if isinstance(node, ast.Call) else ()
    if len(positional) > len(parameters):
        _fail(node, "INVALID_ARGUMENT", "위치 인자가 너무 많습니다.")
    given = {param.name: value for param, value in zip(parameters, positional)}
    names = {param.name for param in parameters}
    for keyword in keywords:
        if keyword.arg not in names or keyword.arg in given:
            _fail(keyword, "INVALID_ARGUMENT", f"지원하지 않거나 중복된 인자입니다. {keyword.arg or '**'}")
        given[keyword.arg] = keyword.value
    result = {}
    for param in parameters:
        if param.name in given:
            result[param.name] = read_value(given[param.name], param, symbols)
            continue
        default = defaults.get(param.name, param.default)
        if default is None and not param.optional:
            _fail(node, "INVALID_ARGUMENT", f"필수 인자가 없습니다. {param.name}")
        if default is not None:
            result[param.name] = default
    return result


def _key(node, key, *, hotkey):
    if not isinstance(key, str):
        _fail(node, "INVALID_ARGUMENT", "키는 문자열 리터럴로 지정하세요.")
    try:
        key = normalize_hotkey(key)
    except ValidationError:
        _fail(node, "INVALID_ARGUMENT", "지원하는 논리 키를 지정하세요.")
    if (not hotkey and "+" in key) or (hotkey and key == DEFAULT_STOP_HOTKEY):
        _fail(node, "INVALID_ARGUMENT", "예약 단축키 또는 이 호출에서 지원하지 않는 조합입니다.")
    return key


def _call(node, symbols, *, read_value=_value, registry=API_REGISTRY):
    if not isinstance(node.func, ast.Attribute) or not isinstance(node.func.value, ast.Name):
        _fail(node, "UNSUPPORTED_SYNTAX", "등록된 API 이름으로 직접 호출하세요.")
    symbol = f"{node.func.value.id}.{node.func.attr}"
    if symbol in OPTIONAL_SERVICES and symbol not in registry:
        _fail(node.func, "UNAVAILABLE_SERVICE", f"등록된 서비스가 없습니다. {symbol}")
    api = registry.get(symbol)
    if api is None:
        _fail(node.func, "UNKNOWN_API", f"지원하지 않는 호출입니다. {symbol}")
    arguments = _bind(node, api.parameters, symbols, read_value=read_value)
    if "key" in arguments:
        arguments["key"] = _key(node, arguments["key"], hotkey=False)
    if symbol == "keyboard.repeat" and all(type(arguments[name]) is int for name in ("frequency_hz", "hold_ms")):
        if arguments["frequency_hz"] * arguments["hold_ms"] >= 1000:
            _fail(node, "INVALID_ARGUMENT", "키 홀드 시간은 반복 주기보다 짧아야 합니다.")
    for name in ("button", "profile_id"):
        if name in arguments and type(arguments[name]) is not str:
            _fail(node, "INVALID_ARGUMENT", f"{name}에는 문자열 리터럴이 필요합니다.")
    return CheckedCall(symbol, tuple(arguments.items()), _span(node))


def _declaration(node, *, header_only=False):
    if not isinstance(node, ast.AsyncFunctionDef):
        _fail(node, "UNSUPPORTED_SYNTAX", "최상위에는 데코레이터가 있는 async def만 선언하세요.")
    _check_name(node, node.name)
    if len(node.decorator_list) != 1:
        _fail(node, "INVALID_DECLARATION", "핸들러나 워크플로 데코레이터를 하나 지정하세요.")
    decorator = node.decorator_list[0]
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not isinstance(target, ast.Name) or target.id not in DECORATORS:
        _fail(decorator, "INVALID_DECLARATION", "지원하는 핸들러 또는 워크플로 데코레이터를 사용하세요.")
    workflow = target.id == "workflow"
    options = _bind(decorator, DECORATORS[target.id], {}, defaults={"name": node.name} if workflow else None)
    if "key" in options:
        options["key"] = _key(decorator, options["key"], hotkey=True)
    name = options.pop("name", node.name)
    signature = node.args
    if (signature.posonlyargs or signature.vararg or signature.kwonlyargs or signature.kwarg
            or signature.defaults or node.returns or node.type_params or (signature.args and not workflow)):
        _fail(node, "INVALID_DECLARATION", "워크플로의 타입 있는 일반 매개변수만 지원합니다.")
    symbols = {}
    for arg in signature.args:
        _check_name(arg, arg.arg)
        if arg.arg in symbols:
            _fail(arg, "DUPLICATE_DECLARATION", f"매개변수 이름이 중복됩니다. {arg.arg}")
        if not isinstance(arg.annotation, ast.Name) or arg.annotation.id not in WORKFLOW_TYPES:
            _fail(arg, "TYPE_ERROR", "매개변수 타입은 int, str, bool 중 하나여야 합니다.")
        symbols[arg.arg] = WORKFLOW_TYPES[arg.annotation.id]
    calls = []
    for statement in () if header_only else node.body:
        match statement:
            case ast.Expr(value=ast.Await(value=ast.Call() as call)):
                calls.append(_call(call, symbols))
            case ast.Pass():
                pass
            case ast.While(test=ast.Constant(value=True)):
                _fail(statement, "UNBOUNDED_LOOP", "PY-1에서는 무한 반복을 허용하지 않습니다.")
            case _:
                _fail(statement, "UNSUPPORTED_SYNTAX", "이 단계에서는 await API 호출과 pass만 지원합니다.")
    return Declaration(name, EVENT_TYPES.get(target.id, "workflow"), tuple(options.items()),
                       tuple(symbols.items()), tuple(calls), _span(node))


def _parse(source):
    if len(source) > MAX_SOURCE_CHARS:
        raise AnalysisError(Diagnostic("SOURCE_LIMIT", "Python 소스 크기 제한을 초과했습니다.", SourceSpan(1, 0, 1, 0)))
    try:
        module = ast.parse(source)
    except SyntaxError as error:
        lines = source.split("\n")

        def byte_column(line, offset):
            if line > len(lines) or offset is None:
                return 0
            return len(lines[line - 1][:max(0, offset - 1)].encode("utf-8", errors="surrogatepass"))

        line, end_line = error.lineno or 1, error.end_lineno or error.lineno or 1
        span = SourceSpan(line, byte_column(line, error.offset), end_line, byte_column(end_line, error.end_offset))
        raise AnalysisError(Diagnostic("SYNTAX_ERROR", error.msg, span)) from None
    except (RecursionError, UnicodeError):
        raise AnalysisError(Diagnostic("SYNTAX_ERROR", "소스의 구문 깊이 또는 인코딩을 확인하세요.",
                                       SourceSpan(1, 0, 1, 0))) from None
    for index, node in enumerate(ast.walk(module)):
        if index >= MAX_AST_NODES:
            raise AnalysisError(Diagnostic("SOURCE_LIMIT", "Python 구문 노드 제한을 초과했습니다.",
                                           SourceSpan(1, 0, 1, 0)))
    return module


def _declarations(module, *, header_only=False):
    declarations = []
    symbols, names, hotkeys = set(), set(), set()
    for node in module.body:
        declaration = _declaration(node, header_only=header_only)
        key = dict(declaration.options).get("key")
        if node.name in symbols or declaration.name in names or key in hotkeys:
            _fail(node, "DUPLICATE_DECLARATION", "함수 이름, 선언 이름 또는 핫키가 중복됩니다.")
        symbols.add(node.name)
        names.add(declaration.name)
        if key is not None:
            hotkeys.add(key)
        declarations.append(declaration)
    return tuple(declarations)


def analyze(source: str) -> tuple[Declaration, ...]:
    """Return checked declarations, or raise AnalysisError for the first diagnostic."""
    return _declarations(_parse(source))
