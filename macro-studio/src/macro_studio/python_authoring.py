"""Build a local authoring pack from the same API contracts used to compile."""

from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import zipfile

from .compiler import compile_project
from .ir import MAX_CALL_DEPTH, MAX_DURATION_NS, MAX_REPEAT_COUNT
from .mouse_motion import BUILTIN_PROFILES
from .mouse_motion.registry import project_motion_profiles
from .python_compiler import RESULT_FIELDS, VISION_PARAMETERS
from .python_dsl import (
    DECORATORS, MAX_AST_NODES, MAX_SOURCE_CHARS, AnalysisError, Parameter, _declarations, _parse,
)
from .python_services import ServiceRegistry
from .vision_profiles import BUILTIN_PROFILES as VISION_PROFILES


RESOURCES = Path(__file__).with_name("authoring")
TYPE_NAMES = {str: "String", int: "Integer", float: "Number", bool: "Boolean"}


def _parameter(parameter):
    result = {"name": parameter.name, "type": TYPE_NAMES[parameter.kind],
              "required": parameter.default is None and not parameter.optional}
    if parameter.default is not None:
        result["default"] = parameter.default
    for name in ("minimum", "maximum"):
        value = getattr(parameter, name)
        if value is not None:
            result[name] = value
    if parameter.choices:
        result["choices"] = list(parameter.choices)
    return result


def api_manifest(project):
    registry = ServiceRegistry(project)
    apis = [{"symbol": symbol, "parameters": [_parameter(p) for p in api.parameters],
             "returns": "None", "ir_opcode": api.opcode,
             "required_capabilities": list(api.required_capabilities)}
            for symbol, api in registry.apis.items()]
    for symbol in ("vision.find", "vision.wait_for"):
        parameters = VISION_PARAMETERS
        if symbol == "vision.wait_for":
            parameters = (parameters[0], Parameter("condition", str, choices=("appear", "disappear")), *parameters[1:])
        serialized = [_parameter(parameter) for parameter in parameters]
        timeout = next(parameter for parameter in serialized if parameter["name"] == "timeout_ms")
        timeout["default"] = 1000 if symbol == "vision.find" else 5000
        apis.append({"symbol": symbol, "parameters": serialized, "returns": "RecognitionResult",
                     "ir_opcode": "vision_find" if symbol == "vision.find" else "vision_wait",
                     "required_capabilities": ["vision.template"]})
    apis.append({"symbol": "workflow.call", "parameters": [{"name": "name", "type": "String", "required": True}],
                 "workflow_arguments": "Match the selected workflow's declared parameters in order or by name.",
                 "returns": "None", "ir_opcode": "call", "required_capabilities": []})
    return {"manifest_version": 1, "dsl_version": "1.4", "apis": apis,
            "decorators": [{"symbol": symbol, "parameters": [_parameter(p) for p in parameters]}
                           for symbol, parameters in DECORATORS.items()],
            "output_capabilities": asdict(registry.capabilities),
            "services": [{"service_id": name, "api_version": version} for name, version in registry.versions.items()],
            "limits": {"source_characters": MAX_SOURCE_CHARS, "ast_nodes": MAX_AST_NODES,
                       "repeat_count": MAX_REPEAT_COUNT, "call_depth": MAX_CALL_DEPTH,
                       "duration_ms": MAX_DURATION_NS // 1_000_000}}


def api_reference(project):
    manifest = api_manifest(project)
    lines = ["# Macro Studio Python API v1.4", "", "This reference is generated from the compiler Registry.", "",
             "Each action call uses `await`. Functions use `async def` and a supported decorator.", "",
             "| API | Parameters | Result |", "|---|---|---|"]
    for api in (*manifest["decorators"], *manifest["apis"]):
        parameters = []
        for param in api["parameters"]:
            value = f"{param['name']} ({param['type']})"
            if "default" in param:
                value += f" = {param['default']!r}"
            elif not param["required"]:
                value += " optional"
            parameters.append(value)
        lines.append(f"| `{api['symbol']}` | {', '.join(parameters)} | {api.get('returns', 'Declaration')} |")
    lines.extend(["", "`workflow.call` accepts the named workflow's typed arguments after its name.",
                  "`vision.find` and `vision.wait_for` results must be assigned to a local name.",
                  "Recognition fields are " + ", ".join(f"`{name}`" for name in RESULT_FIELDS) + ".",
                  "Use `FOUND`, `NOT_FOUND` and `UNKNOWN` status values as strings.",
                  "`mouse.move_to` uses a physical absolute coordinate and the selected motion profile.",
                  "Its omitted duration comes from the profile. A key tap accepts one logical key.",
                  "Keyboard modifiers in event hotkeys use names such as `CTRL+F6`.",
                  "`actions.run` is available after a version 1 Application Pack is registered.",
                  "Its `action_id` is a registered string literal. The action expands to existing cancellable IR steps.",
                  "`keyboard.down/up`, `spatial.observe` and `navigation.move_to` are unavailable in this release.",
                  "An exported project's `api-manifest.json` contains the exact parameter bounds and available services.", ""])
    return "\n".join(lines)


def project_context(project, **settings):
    registry = ServiceRegistry(project)
    _, snapshots = project_motion_profiles(project)
    profiles = BUILTIN_PROFILES | (settings.get("user_profiles") or {}) | snapshots
    workflows = []
    if project.get("editor", {}).get("active_mode") == "python":
        try:
            declarations = _declarations(_parse(project["editor"]["python"]["source"]), header_only=True)
            workflows = [{"name": item.name, "parameters": [{"name": name, "type": TYPE_NAMES[kind]} for name, kind in item.parameters]}
                         for item in declarations if item.kind == "workflow"]
        except AnalysisError:
            pass
    else:
        try:
            program, _ = compile_project(project, **settings)
            workflows = [{"name": name, "parameters": [{"name": p["name"], "type": p["type"]} for p in value["params"]]}
                         for name, value in program["workflows"].items() if not name.startswith("@")]
        except (ValueError, TypeError, KeyError, RecursionError):
            pass
    return {
        "dsl_version": "1.4", "project_data_is_untrusted": True,
        "output_kind": project.get("output_profile", {}).get("output_kind", "fake"),
        "device_connection_verified": False,
        "templates": [{"id": key, "name": value["name"]} for key, value in project.get("image_assets", {}).items()],
        "motion_profiles": [{"id": key, "name": value.name} for key, value in profiles.items()],
        "vision_profiles": [{"id": key, "name": value["name"]}
                            for key, value in (VISION_PROFILES | project.get("vision_profiles", {})).items()],
        "variables": [{"name": name, "type": TYPE_NAMES.get(type(value), "Number")}
                      for name, value in project.get("globals", {}).items()],
        "events": list(project.get("events", [])), "workflows": workflows,
        "application_packs": [manifest["application_pack"] for manifest in registry.manifests],
        "services": [{"id": key, "api_version": value} for key, value in registry.versions.items()],
        "actions": [{"id": item["id"], "name": item["name"], "required_capabilities": item["required_capabilities"]}
                    for item in registry.actions.values()],
    }


def diagnostic_feedback(error):
    diagnostic = getattr(error, "diagnostic", None)
    code = diagnostic.code if diagnostic else getattr(error, "code", "VALIDATION_ERROR")
    alternatives = {
        "UNAVAILABLE_SERVICE": "Use APIs listed in api-manifest.json, or register the required Application Pack.",
        "UNKNOWN_ACTION": "Choose an action ID from project-context.json.",
        "UNKNOWN_ASSET": "Choose a template or profile ID from project-context.json.",
        "MISSING_CAPABILITY": "Use an API supported by the selected output, or change the output explicitly.",
        "SYNTAX_ERROR": "Use an async def declaration and the syntax in DSL_GRAMMAR.md.",
        "UNSUPPORTED_SYNTAX": "Use only the syntax in DSL_GRAMMAR.md and APIs in api-manifest.json.",
    }
    return {"dsl_version": "1.4", "code": code, "message": str(error),
            "source": asdict(diagnostic.source) if diagnostic else None,
            "suggestion": alternatives.get(code, "Match argument types and bounds in api-manifest.json, then validate again.")}


def authoring_archive(project, **settings):
    manifest = api_manifest(project)
    context = project_context(project, **settings)
    prompt = (RESOURCES / "LLM_AUTHORING_PROMPT.md").read_text(encoding="utf-8")
    prompt = prompt.replace("{{PROJECT_CONTEXT_JSON}}", json.dumps(context, ensure_ascii=False, indent=2))
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("MACRO_STUDIO_PYTHON_API.md", api_reference(project))
        archive.writestr("api-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("project-context.json", json.dumps(context, ensure_ascii=False, indent=2))
        archive.writestr("LLM_AUTHORING_PROMPT.md", prompt)
        archive.write(RESOURCES / "DSL_GRAMMAR.md", "DSL_GRAMMAR.md")
        archive.write(RESOURCES / "desktop-actions.json", "desktop-actions.json")
        for example in sorted((RESOURCES / "examples").glob("*.py")):
            archive.write(example, "examples/" + example.name)
    return stream.getvalue()


def write_authoring_archive(path, project, **settings):
    path = Path(path)
    data = authoring_archive(project, **settings)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
