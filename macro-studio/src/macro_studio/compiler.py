from .ir import PARAM_PREFIX, SCREEN_SENSOR_OPS, ValidationError, normalize_hotkey, validate_program
from .mouse_motion import BUILTIN_PROFILES, DEFAULT_PROFILE
from .mouse_motion.registry import project_motion_profiles


def compile_project(project, *, application_profile=DEFAULT_PROFILE, user_profiles=None):
    workspace = project["workspace"]
    workflows = {}
    handlers = []
    states = {}
    transitions = []
    initial = None
    version = 1

    def fail(block, message):
        raise ValidationError(f"({block.get('id', '?')}) {message}")

    def field(block, name):
        return block.get("fields", {}).get(name)

    def child(block, name):
        connection = block.get("inputs", {}).get(name, {})
        return connection.get("block") or connection.get("shadow")

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
        if kind == "random_float":
            return {"op": kind, "args": [expression(value, "A", 0), expression(value, "B", 1)]}
        if kind in ("binary", "arithmetic", "comparison", "logical"):
            default = False if kind == "logical" else 0
            return {"op": field(value, "OP"), "args": [
                expression(value, "A", default), expression(value, "B", default),
            ]}
        if kind == "not":
            return {"op": "not", "args": [expression(value, "VALUE", False)]}
        if kind == "target_window":
            return {"op": kind, "state": field(value, "STATE")}
        if kind in SCREEN_SENSOR_OPS:
            position = position_value(value) if child(value, "POSITION") else {"x": field(value, "X"), "y": field(value, "Y")}
            sensor = {"op": kind, "block_id": value["id"], **position}
            if kind == "pixel_color":
                return dict(sensor, color=field(value, "COLOR"))
            sensor.update(width=expression(value, "WIDTH", field(value, "WIDTH")),
                          height=expression(value, "HEIGHT", field(value, "HEIGHT")))
            if kind == "region_text":
                return dict(sensor, text=field(value, "TEXT"), language=field(value, "LANGUAGE"))
            image = field(value, "IMAGE")
            if not image:
                fail(value, "기준 이미지를 선택하세요.")
            if isinstance(image, str) and image.startswith("asset:") and image not in project.get("image_assets", {}):
                fail(value, "프로젝트에 등록된 기준 이미지를 다시 선택하세요.")
            threshold = value.get("fields", {}).get("SIMILARITY", 1.0)
            return dict(sensor, image=image, min_similarity=threshold)
        fail(value, "지원하지 않는 값 블록입니다.")

    def chain(block, kind):
        while block:
            if block["type"] != kind:
                fail(block, "이 위치에 연결할 수 없는 블록입니다.")
            yield block
            block = block.get("next", {}).get("block")

    top_blocks = workspace.get("blocks", {}).get("blocks", [])
    workflow_parameters = {
        field(block, "NAME"): list(chain(child(block, "PARAMS"), "ms_param_def"))
        for block in top_blocks if block["type"] == "ms_workflow"
    }

    def duration_value(block):
        duration = child(block, "DURATION")
        if not duration or duration["type"] != "ms_duration":
            fail(block, "시간 값 블록을 연결하세요.")
        if not child(duration, "VALUE"):
            fail(duration, "시간 값에 숫자나 연산 블록을 연결하세요.")
        return {"value": expression(duration, "VALUE", None), "unit": field(duration, "UNIT")}

    def hold_time(block):
        milliseconds = field(block, "MS")
        if child(block, "DURATION") or milliseconds is None:
            return {"duration": duration_value(block)}
        return {"hold_ns": round(milliseconds * 1_000_000)}

    def position_value(block):
        position = child(block, "POSITION")
        if not position or position["type"] != "ms_position":
            fail(block, "좌표 블록을 연결하세요.")
        return {axis.lower(): expression(position, axis, field(position, axis)) for axis in ("X", "Y")}

    def keyboard_value(block, *, hotkey=False):
        selected = child(block, "KEY")
        if selected and selected["type"] != "ms_key":
            fail(block, "키 블록을 연결하세요.")
        value = field(selected, "VALUE") if selected else field(block, "KEY")
        if not value:
            fail(block, "키 블록을 연결하고 키를 지정하세요.")
        try:
            key = normalize_hotkey(value)
        except ValidationError as error:
            fail(block, str(error))
        if hotkey and selected and "+" not in key:
            fail(block, "단축키에는 Ctrl, Alt, Shift, Win 중 하나 이상이 필요합니다.")
        if not hotkey and "+" in key:
            fail(block, "키 누르기와 연타에는 단일 키를 연결하세요.")
        return key

    def timed_events(block):
        events = []
        item = child(block, "EVENTS")
        while item:
            offset = field(item, "OFFSET")
            if type(offset) is not int:
                fail(item, "예약 시각은 정수 밀리초로 지정하세요.")
            event = {"offset_us": offset * 1000}
            kind = item["type"]
            action = field(item, "ACTION")
            if kind == "ms_batch_key":
                event.update(action=f"key_{action}", key=keyboard_value(item))
            elif kind == "ms_batch_button":
                event.update(action=f"mouse_{action}", button=field(item, "BUTTON"))
            elif kind == "ms_batch_move":
                event.update(action="mouse_move", dx=field(item, "DX"), dy=field(item, "DY"))
            else:
                fail(item, "예약 입력 블록을 연결하세요.")
            events.append(event)
            item = item.get("next", {}).get("block")
        return events

    def steps(block):
        nonlocal version
        result = []
        while block:
            kind = block["type"].removeprefix("ms_")
            step = {"op": kind, "block_id": block["id"]}
            if block.get("data") == "macro-studio:breakpoint":
                step["breakpoint"] = True
            match kind:
                case "wait":
                    step["duration_ns"] = round(field(block, "MS") * 1_000_000)
                case "wait_time":
                    step.update(op="wait", duration=duration_value(block))
                case "emit_event":
                    step["name"] = field(block, "NAME")
                case "timed_batch":
                    step["events"] = timed_events(block)
                case "mouse_click":
                    step.update(button=field(block, "BUTTON"), **hold_time(block))
                case "mouse_click_at":
                    step.update(position=position_value(block), button=field(block, "BUTTON"), hold_ns=50_000_000)
                case "mouse_move":
                    step.update(position=position_value(block), duration=duration_value(block))
                case "mouse_move_timed" | "mouse_move_relative_timed":
                    version = 2
                    default, snapshots = project_motion_profiles(project)
                    profiles = BUILTIN_PROFILES | (user_profiles or {}) | snapshots
                    ref = field(block, "PROFILE") or "inherit"
                    profile = (profiles[default] if default else application_profile) if ref == "inherit" else profiles.get(ref)
                    if profile is None:
                        fail(block, f"모션 프로파일을 찾을 수 없습니다. {ref}")
                    step.update(
                        op="mouse_move_timed",
                        target={"kind": "absolute" if kind == "mouse_move_timed" else "relative", **position_value(block)},
                        motion={"profile_ref": ref, "profile": profile.to_dict(),
                                "duration": duration_value(block) if child(block, "DURATION") else None},
                    )
                case "key_tap" | "tap_sequence":
                    step.update(key=keyboard_value(block), **hold_time(block))
                    if kind == "tap_sequence":
                        step.update(frequency_hz=expression(block, "HZ", field(block, "HZ")),
                                    count=expression(block, "COUNT", field(block, "COUNT")))
                case "repeat" | "forever" | "while":
                    step["steps"] = steps(child(block, "BODY"))
                    if kind == "repeat":
                        step["count"] = expression(block, "COUNT", 1)
                    elif kind == "while":
                        step["condition"] = expression(block, "CONDITION", False)
                case "if" | "if_only":
                    step.update(op="if", condition=expression(block, "CONDITION", False),
                                then=steps(child(block, "THEN")))
                    if kind == "if":
                        step["else"] = steps(child(block, "ELSE"))
                case "set":
                    step.update(var=field(block, "NAME"), value=expression(block, "VALUE", 0))
                case "call" | "call_store" | "call_if":
                    step["op"] = "call"
                    step["workflow_id"] = field(block, "NAME")
                    args = []
                    legacy = child(block, "ARGS")
                    if legacy:
                        for arg in chain(legacy, "ms_argument"):
                            if not child(arg, "VALUE"):
                                fail(arg, "전달할 인자 값을 연결하세요.")
                            args.append(expression(arg, "VALUE", None))
                    else:
                        for param in workflow_parameters.get(step["workflow_id"], []):
                            socket = "ARG_" + param["id"]
                            if not child(block, socket):
                                fail(block, f"{field(param, 'NAME')} 인자 값을 연결하세요.")
                            args.append(expression(block, socket, None))
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
                case "end" | "break" | "continue":
                    pass
                case _:
                    fail(block, "실행 블록을 이벤트나 워크플로 안에 연결하세요.")
            result.append(step)
            block = block.get("next", {}).get("block")
        return result

    detached = []
    for block in top_blocks:
        kind = block["type"]
        if kind == "ms_workflow":
            name = field(block, "NAME")
            if not name or name.startswith("@") or name in workflows:
                fail(block, "워크플로 이름을 고유하게 지정하세요.")
            workflows[name] = {"steps": steps(child(block, "BODY")), "params": [
                {"name": field(param, "NAME"), "type": field(param, "TYPE"), "block_id": param["id"]}
                for param in workflow_parameters[name]
            ]}
            returns = field(block, "RETURNS")
            if returns and returns != "void":
                workflows[name]["return_type"] = returns
        elif kind in ("ms_start", "ms_hotkey", "ms_interval", "ms_custom"):
            name = "@" + block["id"]
            workflows[name] = {"steps": steps(block.get("next", {}).get("block") or child(block, "BODY"))}
            event = {"type": "project_start"}
            if kind == "ms_hotkey":
                key = keyboard_value(block, hotkey=True)
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
                transition["hotkey"] = keyboard_value(block, hotkey=True)
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
            "ir_version": version, "project_id": project["project_id"],
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
