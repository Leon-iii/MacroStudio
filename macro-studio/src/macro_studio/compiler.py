from .ir import PARAM_PREFIX, SCREEN_SENSOR_OPS, ValidationError, normalize_hotkey, validate_program


def compile_project(project):
    workspace = project["workspace"]
    workflows = {}
    handlers = []
    states = {}
    transitions = []
    initial = None

    def fail(block, message):
        raise ValidationError(f"({block.get('id', '?')}) {message}")

    def field(block, name):
        return block.get("fields", {}).get(name)

    def child(block, name):
        return block.get("inputs", {}).get(name, {}).get("block")

    def expression(block, name, fallback):
        value = child(block, name)
        if value is None:
            return fallback
        kind = value["type"].removeprefix("ms_")
        if kind == "number":
            return field(value, "VALUE")
        if kind == "boolean":
            return field(value, "VALUE") == "TRUE"
        if kind == "text":
            return field(value, "VALUE")
        if kind == "variable":
            return {"var": field(value, "NAME")}
        if kind == "parameter":
            return {"var": PARAM_PREFIX + field(value, "NAME")}
        if kind == "binary":
            return {"op": field(value, "OP"), "args": [
                expression(value, "A", 0), expression(value, "B", 0),
            ]}
        if kind == "not":
            return {"op": "not", "args": [expression(value, "VALUE", False)]}
        if kind == "target_window":
            return {"op": kind, "state": field(value, "STATE")}
        if kind in SCREEN_SENSOR_OPS:
            sensor = {"op": kind, "block_id": value["id"], "x": field(value, "X"), "y": field(value, "Y")}
            if kind == "pixel_color":
                return dict(sensor, color=field(value, "COLOR"))
            sensor.update(width=field(value, "WIDTH"), height=field(value, "HEIGHT"))
            if kind == "region_text":
                return dict(sensor, text=field(value, "TEXT"), language=field(value, "LANGUAGE"))
            return dict(sensor, image=field(value, "IMAGE"))
        fail(value, "지원하지 않는 값 블록입니다.")

    def chain(block, kind):
        while block:
            if block["type"] != kind:
                fail(block, "이 위치에 연결할 수 없는 블록입니다.")
            yield block
            block = block.get("next", {}).get("block")

    def steps(block):
        result = []
        while block:
            kind = block["type"].removeprefix("ms_")
            step = {"op": kind, "block_id": block["id"]}
            if block.get("data") == "macro-studio:breakpoint":
                step["breakpoint"] = True
            match kind:
                case "wait":
                    step["duration_ns"] = round(field(block, "MS") * 1_000_000)
                case "emit_event":
                    step["name"] = field(block, "NAME")
                case "mouse_click":
                    step.update(button=field(block, "BUTTON"), hold_ns=round(field(block, "MS") * 1_000_000))
                case "key_tap" | "tap_sequence":
                    step.update(key=field(block, "KEY"), hold_ns=round(field(block, "MS") * 1_000_000))
                    if kind == "tap_sequence":
                        step.update(frequency_hz=field(block, "HZ"), count=field(block, "COUNT"))
                case "repeat" | "forever":
                    step["steps"] = steps(child(block, "BODY"))
                    if kind == "repeat":
                        step["count"] = expression(block, "COUNT", 1)
                case "if":
                    step.update(condition=expression(block, "CONDITION", False),
                                then=steps(child(block, "THEN")))
                    step["else"] = steps(child(block, "ELSE"))
                case "set":
                    step.update(var=field(block, "NAME"), value=expression(block, "VALUE", 0))
                case "call" | "call_store" | "call_if":
                    step["op"] = "call"
                    step["workflow_id"] = field(block, "NAME")
                    args = []
                    for arg in chain(child(block, "ARGS"), "ms_argument"):
                        if not child(arg, "VALUE"):
                            fail(arg, "전달할 인자 값을 연결하세요.")
                        args.append(expression(arg, "VALUE", None))
                    if args:
                        step["args"] = args
                    if kind == "call_store":
                        step["result"] = field(block, "RESULT")
                    elif kind == "call_if":
                        step["then"] = steps(child(block, "THEN"))
                        step["else"] = steps(child(block, "ELSE"))
                case "return":
                    if not child(block, "VALUE"):
                        fail(block, "반환할 값을 연결하세요.")
                    step["value"] = expression(block, "VALUE", None)
                case "end":
                    pass
                case _:
                    fail(block, "실행 블록을 이벤트나 워크플로 안에 연결하세요.")
            result.append(step)
            block = block.get("next", {}).get("block")
        return result

    detached = []
    for block in workspace.get("blocks", {}).get("blocks", []):
        kind = block["type"]
        if kind == "ms_workflow":
            name = field(block, "NAME")
            if not name or name.startswith("@") or name in workflows:
                fail(block, "워크플로 이름을 고유하게 지정하세요.")
            workflows[name] = {"steps": steps(child(block, "BODY")), "params": [
                {"name": field(param, "NAME"), "type": field(param, "TYPE"), "block_id": param["id"]}
                for param in chain(child(block, "PARAMS"), "ms_param_def")
            ]}
            returns = field(block, "RETURNS")
            if returns and returns != "void":
                workflows[name]["return_type"] = returns
        elif kind in ("ms_start", "ms_hotkey", "ms_interval", "ms_custom"):
            name = "@" + block["id"]
            workflows[name] = {"steps": steps(child(block, "BODY"))}
            event = {"type": "project_start"}
            if kind == "ms_hotkey":
                try:
                    key = normalize_hotkey(field(block, "KEY"))
                except ValidationError as error:
                    fail(block, str(error))
                event = {"type": "hotkey_pressed", "hotkey": key}
            elif kind == "ms_interval":
                milliseconds = field(block, "MS")
                if type(milliseconds) is not int or not 1 <= milliseconds <= 86_400_000:
                    fail(block, "주기는 1~86,400,000 ms 사이의 정수여야 합니다.")
                event = {"type": "interval", "period_ns": milliseconds * 1_000_000}
            elif kind == "ms_custom":
                event = {"type": "custom", "name": field(block, "NAME")}
            handler = {"id": block["id"], "block_id": block["id"], "event": event,
                       "workflow_id": name, "policy": field(block, "POLICY") or "ignore_if_running",
                       "priority": field(block, "PRIORITY") if field(block, "PRIORITY") is not None else 0}
            if child(block, "GUARD"):
                handler["guard"] = expression(block, "GUARD", True)
            handlers.append(handler)
        elif kind == "ms_state":
            name = field(block, "NAME")
            if name in states or not name:
                fail(block, "상태 이름을 고유하게 지정하세요.")
            states[name] = {"workflow_id": field(block, "WORKFLOW") or "",
                            "block_id": block["id"]}
            if field(block, "INITIAL") == "TRUE":
                if initial is not None:
                    fail(block, "시작 상태는 하나만 지정하세요.")
                initial = name
        elif kind in ("ms_state_condition", "ms_state_hotkey"):
            transition = {"from": field(block, "FROM"), "to": field(block, "TO"),
                          "block_id": block["id"]}
            if kind == "ms_state_hotkey":
                transition["hotkey"] = field(block, "KEY")
            if child(block, "GUARD"):
                transition["guard"] = expression(block, "GUARD", False)
            transitions.append(transition)
        else:
            detached.append(block["id"])
    if not handlers and not states and not transitions:
        raise ValidationError("실행할 이벤트 블록을 추가하세요.")
    machine = {"state_machine": {"initial": initial, "states": states, "transitions": transitions}} if states or transitions else {}
    try:
        ir = validate_program({
            "ir_version": 1, "project_id": project["project_id"],
            "globals": project.get("globals", {}), "workflows": workflows, "handlers": handlers,
            **machine,
        })
    except ValidationError as error:
        message = str(error)
        if message.startswith("handlers["):
            index = int(message.split("[", 1)[1].split("]", 1)[0])
            raise ValidationError(f"({handlers[index]['id']}) {message}") from error
        raise
    return ir, detached
