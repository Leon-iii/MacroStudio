"use strict";

const palette = {
  event: "#9a4a0e", input: "#2459a6", time: "#20646d", control: "#356843",
  data: "#70458c", variable: "#a63268", arithmetic: "#795600", logic: "#6440a3",
  workflow: "#59607b", state: "#744756",
};
const input = (name, text) => ({type: "field_input", name, text});
const label = (name, text) => ({type: "field_label", name, text});
const number = (name, value, min, max, precision) => ({
  type: "field_number", name, value, min, max, precision,
});
const dropdown = (name, options) => ({type: "field_dropdown", name, options});
const stack = name => ({type: "input_statement", name});
const value = (name, check) => ({type: "input_value", name, check});
const policy = () => dropdown("POLICY", [
  ["실행 중 무시", "ignore_if_running"], ["다시 시작", "restart"], ["대기열", "queue"],
  ["병렬 실행", "parallel"], ["우선 실행", "override"],
]);
const priority = () => number("PRIORITY", 0, -2147483648, 2147483647, 1);
const valueTypes = [["숫자", "number"], ["불리언", "boolean"], ["문자열", "string"]];
const argumentTypes = {number: ["숫자", "Number"], boolean: ["불리언", "Boolean"], string: ["문자열", "String"]};
const workflowList = (name, check) => ({type: "input_statement", name, check});
const callTypes = new Set(["ms_call", "ms_call_store", "ms_call_if"]);
const argumentInputName = id => "ARG_" + id;

Blockly.Extensions.registerMutator("studio_call_arguments", {
  saveExtraState() {
    return this.argumentParameters ? {parameters: this.argumentParameters} : null;
  },

  loadExtraState({parameters}) {
    this.setArgumentParameters(parameters);
  },

  setArgumentParameters(parameters) {
    const previous = this.argumentParameters ?? [];
    const names = new Set(parameters.map(({id}) => argumentInputName(id)));
    if (this.getInput("ARGS")) this.removeInput("ARGS");
    if (parameters.length && this.getInput("EMPTY_ARGS")) this.removeInput("EMPTY_ARGS");
    for (const {id} of previous) {
      const name = argumentInputName(id);
      if (!names.has(name)) this.removeInput(name);
    }
    for (const {id, name, type} of parameters) {
      const inputName = argumentInputName(id);
      const [label, check] = argumentTypes[type];
      const text = `${name} (${label})`;
      const socket = this.getInput(inputName) ?? this.appendValueInput(inputName).appendField(text, inputName);
      this.getField(inputName).setValue(text);
      socket.setCheck(check);
    }
    if (!parameters.length && !this.getInput("EMPTY_ARGS")) {
      this.appendDummyInput("EMPTY_ARGS").appendField("인자 없음");
    }
    let before = this.getInput("THEN") ? "THEN" : null;
    const order = parameters.length ? parameters.map(({id}) => argumentInputName(id)) : ["EMPTY_ARGS"];
    for (const name of order.reverse()) {
      this.moveInputBefore(name, before);
      before = name;
    }
    this.argumentParameters = parameters;
  },
});
const statement = (type, category, message0, args0 = [], extra = {}) => ({
  type: "ms_" + type, colour: palette[category], message0, args0,
  tooltip: message0.replace(/%\d+/g, "").trim(), previousStatement: null, nextStatement: null, ...extra,
});
const expression = (type, message0, args0, output, category = "data") => ({
  type: "ms_" + type, colour: palette[category], message0, args0, output,
  tooltip: message0.replace(/%\d+/g, "").trim(),
});
const hat = (type, category, message0, args0, extra = {}) => ({
  type: "ms_" + type, colour: palette[category], message0, args0, hat: "cap",
  tooltip: message0.replace(/%\d+/g, "").trim(), ...extra,
});
const arithmeticOperators = [["+", "add"], ["−", "sub"], ["×", "mul"], ["÷", "div"]];
const comparisonOperators = [
  ["=", "eq"], ["≠", "ne"], ["<", "lt"], ["≤", "le"], [">", "gt"], ["≥", "ge"],
];
const logicalOperators = [["그리고", "and"], ["또는", "or"]];
let pickColour;
class StudioColourField extends Blockly.FieldTextInput {
  static fromJson(options) {
    return new StudioColourField(options.text);
  }

  showEditor_() {
    pickColour?.(this.getValue(), colour => {
      if (colour && this.getSourceBlock()) this.setValue(colour);
    });
  }
}
Blockly.fieldRegistry.register("field_studio_colour", StudioColourField);

let listenForKey;
const hotkeyBlocks = new Set(["ms_hotkey", "ms_state_hotkey"]);
const singleKeyBlocks = new Set(["ms_key_tap", "ms_tap_sequence", "ms_batch_key"]);
const directionLabels = new Map([["LEFT", "←"], ["RIGHT", "→"], ["UP", "↑"], ["DOWN", "↓"]]);

function keyLabel(value) {
  return (value ?? "").split("+").map(part => directionLabels.get(part.trim().toUpperCase()) ?? part).join("+");
}

class StudioKeyField extends Blockly.FieldTextInput {
  static fromJson(options) {
    return new StudioKeyField(options.text);
  }

  getText_() {
    return keyLabel(this.getValue());
  }

  isFullBlockField() {
    return false;
  }

  initView() {
    super.initView();
    this.getSvgRoot().classList.add("msKeyField");
  }

  showEditor_() {
    const parent = this.getSourceBlock()?.getParent()?.type;
    const mode = hotkeyBlocks.has(parent) ? "hotkey" : singleKeyBlocks.has(parent) ? "single" : "";
    listenForKey?.(mode, key => {
      if (key && this.getSourceBlock()) this.setValue(key);
    });
  }
}
Blockly.fieldRegistry.register("field_studio_key", StudioKeyField);

let imageAssetOptions = [["이미지 선택", ""]];
class ImageAssetField extends Blockly.FieldDropdown {
  static fromJson() {
    return new ImageAssetField(() => imageAssetOptions);
  }

  getOptions() {
    const options = super.getOptions();
    const selected = this.getValue();
    if (!selected || options.some(([, id]) => id === selected)) return options;
    return [...options, [selected.startsWith("asset:") ? "등록되지 않은 이미지" : selected, selected]];
  }

  doClassValidation_(value) {
    return typeof value === "string" ? value : null;
  }

  refreshAssets() {
    this.doValueUpdate_(this.getValue());
    this.forceRerender();
  }
}
Blockly.fieldRegistry.register("field_studio_image_asset", ImageAssetField);

const propertyNames = {variable: [], event: []};
const propertyField = (name, source) => ({type: "field_studio_property", name, source});

class PropertyField extends Blockly.FieldDropdown {
  static fromJson({source}) {
    const placeholder = source === "variable" ? "변수 선택" : "이벤트 선택";
    return new PropertyField(() => [[placeholder, ""], ...propertyNames[source].map(name => [name, name])]);
  }

  getOptions(useCache) {
    const options = super.getOptions(useCache);
    const value = this.getValue();
    return value && !options.some(([, name]) => name === value)
      ? [...options, [`${value} (미등록)`, value]] : options;
  }

  doClassValidation_(value) {
    return typeof value === "string" ? value : null;
  }

  refreshOptions() {
    this.doValueUpdate_(this.getValue());
    this.forceRerender();
  }
}
Blockly.fieldRegistry.register("field_studio_property", PropertyField);

let motionProfiles = [["프로젝트 기본값", "inherit"]];
class MotionProfileField extends PropertyField {
  static fromJson() {
    return new MotionProfileField(() => motionProfiles);
  }
}
Blockly.fieldRegistry.register("field_studio_motion_profile", MotionProfileField);

const profiledMoveInputs = () => [
  value("POSITION", "Position"), value("DURATION", "Duration"), {type: "input_end_row"},
];
const profiledMoveOptions = {
  message1: "모션 프로파일 %1",
  args1: [{type: "field_studio_motion_profile", name: "PROFILE"}],
  inputsInline: true,
  tooltip: "시간을 비우면 선택한 모션 프로파일의 기본 이동 시간을 사용합니다.",
};

const batchEdge = {previousStatement: "BatchEdge", nextStatement: "BatchEdge", inputsInline: true};
const batchOffset = () => number("OFFSET", 0, 0, 5000, 1);
const batchAction = () => dropdown("ACTION", [["누르기", "down"], ["해제하기", "up"]]);
const eventHats = new Set(["ms_start", "ms_hotkey", "ms_interval", "ms_custom"]);

Blockly.Extensions.register("studio_duration_default", function () {
  this.setFieldValue("sec", "UNIT");
});

Blockly.defineBlocksWithJsonArray([
  hat("start", "event", "%1 %2 우선순위 %3 조건 %4",
    [label("TITLE", "프로젝트 시작"), policy(), priority(), value("GUARD", "Boolean")], {nextStatement: null}),
  hat("hotkey", "event", "%1 %2 %3 %4 %5 우선순위 %6 조건 %7",
    [label("TITLE", "단축키"), label("KEY_OPEN", "("), value("KEY", "Key"), label("KEY_CLOSE", ")"),
     policy(), priority(), value("GUARD", "Boolean")], {nextStatement: null, inputsInline: true}),
  hat("interval", "event", "%1 %2 %3 우선순위 %4 조건 %5",
    [number("MS", 1000, 1, 86400000, 1), label("TITLE", "ms마다"),
     policy(), priority(), value("GUARD", "Boolean")], {nextStatement: null}),
  hat("custom", "event", "%1 %2 %3 %4 우선순위 %5 조건 %6",
    [label("TITLE", "이벤트"), propertyField("NAME", "event"), label("TITLE_END", "를 받으면"),
     policy(), priority(), value("GUARD", "Boolean")], {nextStatement: null}),
  statement("emit_event", "event", "이벤트 %1 발행", [propertyField("NAME", "event")]),
  expression("key", "%1", [{type: "field_studio_key", name: "VALUE", text: "A"}], "Key", "input"),
  statement("key_tap", "input", "%1 키를 %2 동안 누르기", [value("KEY", "Key"), value("DURATION", "Duration")],
    {inputsInline: true}),
  statement("mouse_click", "input", "마우스 %1 버튼을 %2 동안 누르기",
    [dropdown("BUTTON", [["왼쪽", "left"], ["오른쪽", "right"], ["가운데", "middle"]]),
     value("DURATION", "Duration")], {inputsInline: true}),
  {...expression("position", "좌표 X %1 Y %2", [value("X", "Number"), value("Y", "Number")], "Position"),
    colour: palette.input, inputsInline: true},
  statement("mouse_move", "input", "마우스를 %1 로 %2 동안 움직이기",
    [value("POSITION", "Position"), value("DURATION", "Duration")], {inputsInline: true}),
  statement("mouse_move_timed", "input", "마우스를 %1 로 %2 동안 이동 %3",
    profiledMoveInputs(), profiledMoveOptions),
  statement("mouse_move_relative_timed", "input", "마우스를 Δ %1 만큼 %2 동안 이동 %3",
    profiledMoveInputs(), profiledMoveOptions),
  statement("mouse_click_at", "input", "%1 를 %2 하기", [value("POSITION", "Position"),
    dropdown("BUTTON", [["좌클릭", "left"], ["우클릭", "right"], ["가운데 클릭", "middle"]])], {inputsInline: true}),
  statement("tap_sequence", "input", "%1 키 연타 %2 Hz · %3 · %4 회",
    [value("KEY", "Key"), value("HZ", "Number"), value("DURATION", "Duration"), value("COUNT", "Number")],
    {inputsInline: true}),
  statement("timed_batch", "time", "예약 입력 묶음 (Timed Batch)", [],
    {message1: "%1", args1: [workflowList("EVENTS", "BatchEdge")]}),
  statement("batch_key", "time", "%1 ms에 %2 키 %3",
    [batchOffset(), value("KEY", "Key"), batchAction()], batchEdge),
  statement("batch_button", "time", "%1 ms에 %2 버튼 %3",
    [batchOffset(), dropdown("BUTTON", [["왼쪽", "left"], ["오른쪽", "right"], ["가운데", "middle"]]),
     batchAction()], batchEdge),
  statement("batch_move", "time", "%1 ms에 마우스 ΔX %2 ΔY %3",
    [batchOffset(), number("DX", 0, -127, 127, 1), number("DY", 0, -127, 127, 1)], batchEdge),
  statement("wait", "time", "%1 ms만큼 기다리기", [number("MS", 100, 0, 86400000, 0.001)]),
  statement("wait_time", "time", "%1 만큼 기다리기", [value("DURATION", "Duration")], {inputsInline: true}),
  {...expression("duration", "%1 %2", [value("VALUE", "Number"),
    dropdown("UNIT", [["ms", "ms"], ["sec", "sec"], ["min", "min"]])], "Duration"),
    colour: palette.time, inputsInline: true, extensions: ["studio_duration_default"],
    tooltip: "숫자와 단위를 묶어 시간을 지정합니다."},
  statement("repeat", "control", "%1 회 반복", [value("COUNT", "Number")],
    {message1: "%1", args1: [stack("BODY")], inputsInline: true}),
  statement("forever", "control", "계속 반복", [], {message1: "%1", args1: [stack("BODY")]}),
  statement("while", "control", "%1 인 동안 반복", [value("CONDITION", "Boolean")],
    {message1: "%1", args1: [stack("BODY")], inputsInline: true}),
  statement("if_only", "control", "만약 %1", [value("CONDITION", "Boolean")],
    {message1: "%1", args1: [stack("THEN")], inputsInline: true}),
  statement("if", "control", "만약 %1 실행 %2 그 외 %3", [value("CONDITION", "Boolean"), stack("THEN"), stack("ELSE")]),
  statement("break", "control", "반복 탈출", [], {nextStatement: undefined}),
  statement("continue", "control", "다음 반복", [], {nextStatement: undefined}),
  statement("end", "control", "이 작업 종료", [], {nextStatement: undefined}),
  statement("set", "variable", "변수 %1 에 %2 저장", [propertyField("NAME", "variable"), value("VALUE")]),
  expression("number", "%1", [number("VALUE", 1)], "Number", "arithmetic"),
  expression("boolean", "%1", [dropdown("VALUE", [["참", "TRUE"], ["거짓", "FALSE"]])], "Boolean", "logic"),
  expression("text", "문자열 %1", [input("VALUE", "")], "String", "variable"),
  expression("variable", "변수 %1", [propertyField("NAME", "variable")], null, "variable"),
  {...expression("binary", "%1 %2 %3", [value("A"),
    dropdown("OP", [...arithmeticOperators, ...comparisonOperators, ...logicalOperators]), value("B")], null),
    inputsInline: true},
  {...expression("arithmetic", "%1 %2 %3", [value("A", "Number"),
    dropdown("OP", arithmeticOperators), value("B", "Number")], "Number", "arithmetic"), inputsInline: true},
  {...expression("random_float", "%1 ~ %2 사이 무작위 실수", [value("A", "Number"), value("B", "Number")],
    "Number", "arithmetic"), inputsInline: true},
  {...expression("comparison", "%1 %2 %3", [value("A"), dropdown("OP", comparisonOperators), value("B")], "Boolean", "logic"),
    inputsInline: true},
  {...expression("logical", "%1 %2 %3", [value("A", "Boolean"),
    dropdown("OP", logicalOperators), value("B", "Boolean")], "Boolean", "logic"),
    inputsInline: true},
  expression("not", "%1 의 반대", [value("VALUE", "Boolean")], "Boolean", "logic"),
  {...expression("pixel_color", "픽셀 %1 색상 %2 일치",
    [value("POSITION", "Position"), {type: "field_studio_colour", name: "COLOR", text: "#ffffff"}],
    "Boolean"), colour: "#23627c", inputsInline: true},
  {...expression("target_window", "대상 창 상태가 %1",
    [dropdown("STATE", [["전면", "foreground"], ["열려 있음", "exists"], ["최소화", "minimized"]])],
    "Boolean"), colour: "#23627c"},
  {...expression("region_image", "영역 %1 너비 %2 높이 %3 에 이미지 %4 있음 · 최소 일치도 %5",
    [value("POSITION", "Position"), value("WIDTH", "Number"), value("HEIGHT", "Number"),
     {type: "field_studio_image_asset", name: "IMAGE"}, number("SIMILARITY", 1, 0, 1)], "Boolean"),
    colour: "#23627c", inputsInline: true},
  {...expression("region_text", "영역 %1 너비 %2 높이 %3 에 텍스트 %4 있음 %5",
    [value("POSITION", "Position"), value("WIDTH", "Number"), value("HEIGHT", "Number"), input("TEXT", "READY"),
     dropdown("LANGUAGE", [["영어", "eng"], ["한국어", "kor"], ["한국어+영어", "kor+eng"]])],
    "Boolean"), colour: "#23627c", inputsInline: true},
  hat("workflow", "workflow", "%1 반환 타입 %2",
    [input("NAME", "작업"), dropdown("RETURNS", [["없음", "void"], ...valueTypes])],
    {message1: "매개변수 %1", args1: [workflowList("PARAMS", "ParameterList")],
     message2: "%1", args2: [stack("BODY")]}),
  statement("param_def", "workflow", "매개변수 %1 타입 %2",
    [input("NAME", "값"), dropdown("TYPE", valueTypes)],
    {previousStatement: "ParameterList", nextStatement: "ParameterList"}),
  expression("parameter", "매개변수 %1 값", [input("NAME", "값")], null),
  statement("argument", "workflow", "전달할 값 %1", [value("VALUE")],
    {previousStatement: "ArgumentList", nextStatement: "ArgumentList"}),
  statement("call", "workflow", "워크플로 %1 호출", [input("NAME", "작업")],
    {message1: "인자 %1", args1: [workflowList("ARGS", "ArgumentList")], mutator: "studio_call_arguments"}),
  statement("call_store", "workflow", "워크플로 %1 결과를 변수 %2 에 저장",
    [input("NAME", "작업"), propertyField("RESULT", "variable")],
    {message1: "인자 %1", args1: [workflowList("ARGS", "ArgumentList")], mutator: "studio_call_arguments"}),
  statement("call_if", "workflow", "워크플로 %1 결과가 참이면", [input("NAME", "작업")],
    {message1: "인자 %1", args1: [workflowList("ARGS", "ArgumentList")],
     message2: "실행 %1 그 외 %2", args2: [stack("THEN"), stack("ELSE")], mutator: "studio_call_arguments"}),
  statement("return", "workflow", "%1 반환", [value("VALUE")], {nextStatement: undefined}),
  hat("state", "state", "상태 %1 시작 상태 %2 진입 워크플로 %3",
    [input("NAME", "대기"), dropdown("INITIAL", [["아니요", "FALSE"], ["예", "TRUE"]]), input("WORKFLOW", "")]),
  hat("state_condition", "state", "전이 %1 에서 %2 로 조건 %3",
    [input("FROM", "대기"), input("TO", "실행"), value("GUARD", "Boolean")]),
  hat("state_hotkey", "state", "전이 %1 에서 %2 로 핫키 %3 추가 조건 %4",
    [input("FROM", "실행"), input("TO", "대기"), value("KEY", "Key"), value("GUARD", "Boolean")]),
]);

const categories = [
  ["이벤트", "event", ["start", "hotkey", "interval", "custom", "emit_event"]],
  ["키보드", "input", ["key", "key_tap", "tap_sequence"]],
  ["마우스", "input", ["position", "mouse_move", "mouse_move_timed", "mouse_move_relative_timed",
                     "mouse_click_at", "mouse_click"]],
  ["시간", "time", ["duration", "wait_time", "timed_batch", "batch_key", "batch_button", "batch_move"]],
  ["제어", "control", ["repeat", "forever", "while", "if_only", "if", "break", "continue", "end"]],
  ["변수", "variable", ["set", "variable", "text"]],
  ["산술 연산", "arithmetic", ["number", "arithmetic", "random_float"]],
  ["논리 연산", "logic", ["boolean", "comparison", "logical", "not"]],
  ["화면 센서", "screen", ["pixel_color", "region_image", "region_text", "target_window"]],
  ["워크플로", "workflow", ["workflow", "param_def", "parameter", "call", "call_store", "call_if", "return"]],
  ["상태 머신", "state", ["state", "state_condition", "state_hotkey"]],
];

const numberShadow = VALUE => ({shadow: {type: "ms_number", fields: {VALUE}}});
const booleanShadow = enabled => ({shadow: {type: "ms_boolean", fields: {VALUE: enabled ? "TRUE" : "FALSE"}}});
const keyShadow = VALUE => ({shadow: {type: "ms_key", fields: {VALUE}}});
const durationInputs = {VALUE: numberShadow(1)};
const durationInput = (amount, unit) => ({block: {type: "ms_duration", fields: {UNIT: unit},
  inputs: {VALUE: numberShadow(amount)}}});
const defaultDuration = durationInput(1, "sec");
const defaultHold = {DURATION: durationInput(50, "ms")};
const positionInputs = {X: numberShadow(0), Y: numberShadow(0)};
const defaultPosition = {block: {type: "ms_position", inputs: positionInputs}};
const defaultRegion = {POSITION: defaultPosition, WIDTH: numberShadow(320), HEIGHT: numberShadow(240)};
const optionalGuard = {GUARD: booleanShadow(true)};
const inactiveCondition = {CONDITION: booleanShadow(false)};
const toolboxInputs = {
  start: optionalGuard,
  hotkey: {...optionalGuard, KEY: keyShadow("CTRL+F7")},
  interval: optionalGuard,
  custom: optionalGuard,
  duration: durationInputs,
  key_tap: {...defaultHold, KEY: keyShadow("A")},
  mouse_click: defaultHold,
  tap_sequence: {KEY: keyShadow("A"), HZ: numberShadow(100), DURATION: durationInput(2, "ms"), COUNT: numberShadow(10)},
  batch_key: {KEY: keyShadow("A")},
  position: positionInputs,
  wait_time: {DURATION: defaultDuration},
  mouse_move: {POSITION: defaultPosition, DURATION: defaultDuration},
  mouse_move_timed: {POSITION: defaultPosition},
  mouse_move_relative_timed: {POSITION: defaultPosition},
  mouse_click_at: {POSITION: defaultPosition},
  pixel_color: {POSITION: defaultPosition},
  region_image: defaultRegion,
  region_text: defaultRegion,
  repeat: {COUNT: numberShadow(1)},
  while: inactiveCondition,
  if_only: inactiveCondition,
  if: inactiveCondition,
  arithmetic: {A: numberShadow(0), B: numberShadow(1)},
  random_float: {A: numberShadow(0), B: numberShadow(1)},
  comparison: {A: numberShadow(0), B: numberShadow(1)},
  logical: {A: booleanShadow(false), B: booleanShadow(true)},
  not: {VALUE: booleanShadow(false)},
  state_condition: {GUARD: booleanShadow(false)},
  state_hotkey: {...optionalGuard, KEY: keyShadow("CTRL+F7")},
};

const headerWidth = 276;
const headerHeight = 43;
const headerPadding = 10;
const headerFieldGap = 6;
const workflowBodyInset = 12;
const headerFields = {
  ms_start: ["TITLE"],
  ms_hotkey: ["TITLE", "KEY_OPEN", "KEY", "KEY_CLOSE"],
  ms_interval: ["MS", "TITLE"],
  ms_custom: ["TITLE", "NAME", "TITLE_END"],
  ms_workflow: ["NAME"],
};
const headerFont = '600 13px "Malgun Gothic", sans-serif';
const textMetrics = document.createElement("canvas").getContext("2d");
textMetrics.font = headerFont;

function ellipsizeHeader(text) {
  const width = headerWidth - 24;
  if (text.length <= 160 && textMetrics.measureText(text).width <= width) return text;
  const letters = Array.from(text.slice(0, 160));
  while (letters.length && textMetrics.measureText(letters.join("") + "…").width > width) {
    letters.pop();
  }
  return letters.join("") + "…";
}

function headerLines(block) {
  const field = name => block.getField(name)?.getText() || "";
  if (headerFields[block.type]) {
    if (!block.isCollapsed()) return [];
    if (block.type === "ms_hotkey") {
      const key = keyLabel(block.getInputTargetBlock("KEY")?.getFieldValue("VALUE"));
      return [key ? `단축키 (${key})` : "단축키"];
    }
    return [headerFields[block.type].map(field).join(" ")];
  }
  const condition = block.getInputTargetBlock("GUARD")?.toString(120);
  const guard = condition ? "조건 · " + condition : "조건 없음";
  switch (block.type) {
    case "ms_state": return ["상태 · " + field("NAME")];
    case "ms_state_condition":
      return [field("FROM") + " → " + field("TO"), condition ? guard : "조건 연결 필요"];
    case "ms_state_hotkey": return [field("FROM") + " → " + field("TO"),
      (keyLabel(block.getInputTargetBlock("KEY")?.getFieldValue("VALUE")) || "키 선택") + (condition ? " · " + condition : "")];
    default: return [];
  }
}

function updateBlockHeader(block) {
  if (!block || block.previousConnection || block.outputConnection || !block.getSvgRoot()) return;
  const root = block.getSvgRoot();
  const lines = headerLines(block);
  let group = root.querySelector(":scope > .msBlockHeader");
  if (!lines.length) {
    group?.remove();
    return;
  }
  if (!group) {
    group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.setAttribute("class", "msBlockHeader");
    group.setAttribute("aria-hidden", "true");
    root.appendChild(group);
  }
  const label = lines.join("\n");
  if (group.dataset.label === label) return;
  group.dataset.label = label;
  group.replaceChildren(...lines.map((line, index) => {
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", "12");
    text.setAttribute("y", lines.length === 1 ? "26" : String(17 + index * 17));
    text.textContent = ellipsizeHeader(line);
    return text;
  }));
}

function headerShape(width, height) {
  const radius = 7;
  const side = height - radius;
  const horizontalPerVertical = 1 / Math.sqrt(3);
  const cornerRun = radius * horizontalPerVertical;
  const sideRun = side * horizontalPerVertical;
  return {
    width: width + cornerRun + sideRun,
    height,
    path: `v-${side} q0 -${radius} ${radius} -${radius} h${width - 2 * radius} q${radius} 0 ${radius + cornerRun} ${radius} l${sideRun} ${side}`,
  };
}

class StudioZelosConstants extends Blockly.zelos.ConstantProvider {
  makeStartHat() {
    return headerShape(headerWidth, headerHeight);
  }
}

class StudioZelosRenderInfo extends Blockly.zelos.RenderInfo {
  createRows_() {
    super.createRows_();
    const params = this.block_.getInput("PARAMS")?.connection;
    const args = this.block_.getInput("ARGS")?.connection;
    const listConnection = params || args;
    if (listConnection && !listConnection.targetBlock()) {
      const listRow = this.inputRows.find(row => row.getLastInput()?.connectionModel === listConnection);
      if (listRow) {
        // A single parameter row is 2 px taller than a single argument row in Zelos.
        listRow.getLastInput().height = this.constants_.MIN_BLOCK_HEIGHT + (params ? 2 : 0);
      }
    }

    const names = headerFields[this.block_.type];
    const hat = this.topRow.elements.find(element => Blockly.blockRendering.Types.isHat(element));
    if (!names || !hat || this.block_.isCollapsed()) return;
    const keyConnection = this.block_.getInput("KEY")?.connection;
    const elements = this.inputRows.flatMap(row => row.elements);
    this.headerElements = names.map(name => {
      const field = this.block_.getField(name);
      return elements.find(element => field ? element.field === field : element.connectionModel === keyConnection);
    });
    this.headerContentHeight = Math.max(...this.headerElements.map(element => element.height));
    const contentWidth = this.headerElements.reduce((sum, element) => sum + element.width, 0)
      + headerFieldGap * (this.headerElements.length - 1);
    this.headerShape = headerShape(contentWidth + 2 * headerPadding, this.headerContentHeight + 2 * headerPadding);
    Object.assign(hat, {width: this.headerShape.width, height: this.headerShape.height,
      ascenderHeight: this.headerShape.height});
    for (const row of this.inputRows) {
      row.elements = row.elements.filter(element => !this.headerElements.includes(element));
    }
  }

  alignStatementRow_(row) {
    super.alignStatementRow_(row);
    if (this.block_.type !== "ms_workflow") return;
    const input = row.getLastInput();
    if (input.connectionModel !== this.block_.getInput("BODY").connection) return;

    const shift = workflowBodyInset - row.statementEdge;
    row.getFirstSpacer().width += shift;
    input.width -= shift;
    row.statementEdge = workflowBodyInset;
    row.widthWithConnectedBlocks = Math.max(row.width, row.statementEdge + row.connectedBlockWidths);
  }
}

class StudioZelosDrawer extends Blockly.zelos.Drawer {
  constructor(block, info) {
    super(block, info);
    if (info.headerShape) {
      this.constants_ = Object.assign(Object.create(this.constants_), {START_HAT: info.headerShape});
    }
  }

  drawInternals_() {
    super.drawInternals_();
    if (!this.info_.headerElements) return;
    let x = headerPadding;
    for (const element of this.info_.headerElements) {
      element.xPos = x;
      element.centerline = headerPadding + this.info_.headerContentHeight / 2;
      if (element.field) this.layoutField_(element);
      else this.drawInlineInput_(element);
      x += element.width + headerFieldGap;
    }
  }
}

class StudioZelosRenderer extends Blockly.zelos.Renderer {
  makeConstants_() {
    return new StudioZelosConstants();
  }

  makeRenderInfo_(block) {
    return new StudioZelosRenderInfo(this, block);
  }

  makeDrawer_(block, info) {
    updateBlockHeader(block);
    return new StudioZelosDrawer(block, info);
  }
}

Blockly.blockRendering.register("studio_zelos", StudioZelosRenderer);
const theme = Blockly.Theme.defineTheme("studio", {
  base: Blockly.Themes.Classic,
  componentStyles: {
    workspaceBackgroundColour: "#e9edf2", toolboxBackgroundColour: "#f7f9fc",
    toolboxForegroundColour: "#21334b", flyoutBackgroundColour: "#dfe6ee",
    flyoutForegroundColour: "#172536", flyoutOpacity: 1, scrollbarColour: "#8393a6",
    insertionMarkerColour: "#315de0", insertionMarkerOpacity: 0.4,
  },
  fontStyle: {family: '"Malgun Gothic", sans-serif', weight: "600", size: 12},
  startHats: true,
});
const workflowCategory = "macro-studio-workflows";
const toolboxBlock = type => ({kind: "block", type: "ms_" + type, inputs: toolboxInputs[type]});
const workspace = Blockly.inject("workspace", {
  toolbox: {kind: "categoryToolbox", contents: categories.map(([name, color, blocks]) => ({
    kind: "category", name, colour: color === "screen" ? "#23627c" : palette[color],
    ...(color === "workflow" ? {toolboxitemid: workflowCategory, custom: workflowCategory}
      : {contents: blocks.map(toolboxBlock)}),
  }))},
  theme, renderer: "studio_zelos", media: "./media/",
  grid: {spacing: 24, length: 2, colour: "#b9c4d2", snap: true},
  zoom: {controls: true, wheel: false, startScale: 0.92, minScale: 0.45, maxScale: 1.5},
  move: {scrollbars: true, drag: true, wheel: true},
});

const workspaceSvg = workspace.getParentSvg();
let rightPan;

workspaceSvg.addEventListener("pointerdown", event => {
  if (event.pointerType !== "mouse" || event.button !== 2 || event.buttons !== 2 ||
      !event.target.classList.contains("blocklyMainBackground")) return;
  event.preventDefault();
  event.stopPropagation();
  workspace.markFocused();
  workspace.hideChaff();
  rightPan = {id: event.pointerId, x: event.clientX, y: event.clientY,
    scrollX: workspace.scrollX, scrollY: workspace.scrollY, dragging: false};
  workspaceSvg.setPointerCapture(event.pointerId);
}, true);

function panWorkspace(event) {
  const dx = event.clientX - rightPan.x;
  const dy = event.clientY - rightPan.y;
  if (!rightPan.dragging && Math.hypot(dx, dy) < 4) return;
  if (!rightPan.dragging) {
    rightPan.dragging = true;
    workspaceSvg.classList.add("msPanning");
  }
  workspace.scroll(rightPan.scrollX + dx, rightPan.scrollY + dy);
}

function endRightPan(event) {
  if (rightPan?.id !== event.pointerId) return;
  rightPan = undefined;
  workspaceSvg.classList.remove("msPanning");
}

workspaceSvg.addEventListener("pointermove", event => {
  if (rightPan?.id !== event.pointerId) return;
  event.stopPropagation();
  if (event.buttons & 2) panWorkspace(event);
  else endRightPan(event);
}, true);
workspaceSvg.addEventListener("pointerup", event => {
  if (rightPan?.id !== event.pointerId) return;
  event.stopPropagation();
  panWorkspace(event);
  const dragged = rightPan.dragging;
  endRightPan(event);
  if (!dragged) workspace.showContextMenu(event);
}, true);
workspaceSvg.addEventListener("pointercancel", endRightPan);
workspaceSvg.addEventListener("lostpointercapture", endRightPan);

const workflowTools = categories.find(([, color]) => color === "workflow")[2];
let shownWorkflowSignatures = "";

function workflowSignatures() {
  const signatures = new Map();
  for (const block of workspace.getTopBlocks(false)) {
    if (block.type !== "ms_workflow") continue;
    const name = block.getFieldValue("NAME");
    if (!name.trim() || name.startsWith("@") || signatures.has(name)) continue;
    const parameters = [];
    for (let param = block.getInputTargetBlock("PARAMS"); param; param = param.getNextBlock()) {
      parameters.push({id: param.id, name: param.getFieldValue("NAME"), type: param.getFieldValue("TYPE")});
    }
    signatures.set(name, parameters);
  }
  return [...signatures].sort(([first], [second]) => first.localeCompare(second, "ko"));
}

workspace.registerToolboxCategoryCallback(workflowCategory, () => {
  const signatures = workflowSignatures();
  shownWorkflowSignatures = JSON.stringify(signatures);
  const projectBlocks = signatures.map(([NAME, parameters]) => ({
    ...toolboxBlock("call"), fields: {NAME}, extraState: {parameters},
  }));
  return [
    {kind: "label", text: projectBlocks.length ? "프로젝트 워크플로" : "정의한 워크플로 없음"},
    ...projectBlocks,
    {kind: "label", text: "워크플로 도구"},
    ...workflowTools.map(toolboxBlock),
  ];
});

function refreshWorkflowCategory(signatures) {
  const toolbox = workspace.getToolbox();
  if (toolbox.getSelectedItem()?.getId() !== workflowCategory || workspace.isDragging()) return;
  if (JSON.stringify(signatures) !== shownWorkflowSignatures) toolbox.refreshSelection();
}

function syncCallArguments(signatures) {
  const definitions = new Map(signatures);
  Blockly.Events.disable();
  try {
    for (const block of workspace.getAllBlocks(false)) {
      if (!callTypes.has(block.type)) continue;
      const parameters = definitions.get(block.getFieldValue("NAME"));
      const legacy = block.getInputTargetBlock("ARGS");
      if (!parameters && (legacy || block.argumentParameters?.length)) continue;
      const signature = parameters ?? [];
      if (legacy) {
        const values = [];
        for (let arg = legacy; arg; arg = arg.getNextBlock()) {
          values.push(arg.getInput("VALUE").connection);
        }
        if (values.length > signature.length || values.some((connection, index) => {
          const checks = connection.targetConnection?.getCheck();
          return checks && !checks.includes(argumentTypes[signature[index].type][1]);
        })) continue;
        const saved = values.map(connection => {
          const target = connection.targetBlock();
          const shadow = connection.getShadowState();
          connection.setShadowState(null);
          if (target && !target.isShadow() && connection.isConnected()) connection.disconnect();
          return {target: target?.isShadow() ? null : target?.outputConnection, shadow};
        });
        legacy.dispose(false);
        block.setArgumentParameters(signature);
        saved.forEach(({target, shadow}, index) => {
          const connection = block.getInput(argumentInputName(signature[index].id)).connection;
          if (target) connection.connect(target);
          if (shadow) connection.setShadowState(shadow);
        });
      } else if (JSON.stringify(signature) !== JSON.stringify(block.argumentParameters)) {
        block.setArgumentParameters(signature);
      }
    }
  } finally {
    Blockly.Events.enable();
  }
}

function upgradeBlockInputs(block) {
  if (eventHats.has(block.type) && block.inputs?.BODY) {
    block.next ??= block.inputs.BODY;
    delete block.inputs.BODY;
  }
  if (["ms_custom", "ms_emit_event"].includes(block.type) && typeof block.fields?.NAME === "string") {
    block.fields.NAME = block.fields.NAME.trim();
  }
  const isRegion = block.type === "ms_region_image" || block.type === "ms_region_text";
  if ((hotkeyBlocks.has(block.type) || singleKeyBlocks.has(block.type)) && typeof block.fields?.KEY === "string") {
    block.inputs ??= {};
    block.inputs.KEY ??= keyShadow(block.fields.KEY);
    delete block.fields.KEY;
  }
  if (["ms_key_tap", "ms_tap_sequence", "ms_mouse_click"].includes(block.type) && block.fields) {
    block.inputs ??= {};
    if ("MS" in block.fields) {
      block.inputs.DURATION ??= durationInput(block.fields.MS, "ms");
      delete block.fields.MS;
    }
    if (block.type === "ms_tap_sequence") {
      for (const name of ["HZ", "COUNT"]) {
        if (name in block.fields) {
          block.inputs[name] ??= numberShadow(block.fields[name]);
          delete block.fields[name];
        }
      }
    }
  }
  if ((isRegion || block.type === "ms_pixel_color") && block.fields) {
    block.inputs ??= {};
    if ("X" in block.fields || "Y" in block.fields) {
      block.inputs.POSITION ??= {block: {type: "ms_position", fields: {X: block.fields.X ?? 0, Y: block.fields.Y ?? 0}}};
      delete block.fields.X;
      delete block.fields.Y;
    }
  }
  if ((isRegion || block.type === "ms_position") && block.fields) {
    block.inputs ??= {};
    for (const name of isRegion ? ["WIDTH", "HEIGHT"] : ["X", "Y"]) {
      if (name in block.fields) {
        block.inputs[name] ??= numberShadow(block.fields[name]);
        delete block.fields[name];
      }
    }
  }
  for (const connection of Object.values(block.inputs ?? {})) {
    if (connection.shadow?.type === "ms_position") {
      connection.block ??= connection.shadow;
      delete connection.shadow;
    }
    if (connection.block) upgradeBlockInputs(connection.block);
  }
  if (block.next?.block) upgradeBlockInputs(block.next.block);
}

new QWebChannel(qt.webChannelTransport, channel => {
  const bridge = channel.objects.bridge;
  pickColour = (initial, done) => bridge.chooseColour(initial, done);
  listenForKey = (mode, done) => bridge.chooseKey(mode, done);
  function refreshMotionProfile(block) {
    const field = block?.getField("PROFILE");
    if (!(field instanceof MotionProfileField)) return;
    field.refreshOptions();
    const id = field.getValue();
    block.setWarningText(motionProfiles.some(([, value]) => value === id)
      ? null : "모션 프로파일을 찾을 수 없습니다.");
  }
  bridge.motionProfilesChanged.connect(data => {
    motionProfiles = [["프로젝트 기본값", "inherit"], ...JSON.parse(data)];
    for (const editor of [workspace, workspace.getToolbox().getFlyout().getWorkspace()]) {
      for (const block of editor.getAllBlocks(false)) refreshMotionProfile(block);
    }
  });
  bridge.propertyOptionsChanged.connect(data => {
    Object.assign(propertyNames, JSON.parse(data));
    for (const editor of [workspace, workspace.getToolbox().getFlyout().getWorkspace()]) {
      for (const block of editor.getAllBlocks(false)) {
        for (const input of block.inputList) {
          for (const field of input.fieldRow) {
            if (field instanceof PropertyField) field.refreshOptions();
          }
        }
      }
    }
  });
  bridge.imageAssetsChanged.connect(data => {
    const options = Object.entries(JSON.parse(data)).map(([id, name]) => [name, id]);
    options.sort(([first], [second]) => first.localeCompare(second, "ko"));
    imageAssetOptions = [["이미지 선택", ""], ...options];
    for (const editor of [workspace, workspace.getToolbox().getFlyout().getWorkspace()]) {
      for (const block of editor.getAllBlocks(false)) {
        if (block.type === "ms_region_image") block.getField("IMAGE").refreshAssets();
      }
    }
  });
  let workspacePublishQueued = false;
  let argumentSyncNeeded = false;
  const headersToRefresh = new Set();
  function scheduleWorkspacePublish() {
    if (workspacePublishQueued) return;
    workspacePublishQueued = true;
    queueMicrotask(() => {
      workspacePublishQueued = false;
      if (argumentSyncNeeded && !workspace.isDragging()) {
        argumentSyncNeeded = false;
        const signatures = workflowSignatures();
        syncCallArguments(signatures);
        refreshWorkflowCategory(signatures);
      }
      for (const block of headersToRefresh) updateBlockHeader(block);
      headersToRefresh.clear();
      bridge.changed(JSON.stringify(Blockly.serialization.workspaces.save(workspace)));
    });
  }
  Blockly.ContextMenuRegistry.registry.register({
    id: "macro-studio-block-help",
    scopeType: Blockly.ContextMenuRegistry.ScopeType.BLOCK,
    weight: 7,
    displayText: () => "도움말",
    preconditionFn: ({block}) => block?.type.startsWith("ms_") ? "enabled" : "hidden",
    callback: ({block}) => bridge.requestHelp(block.type),
  });
  const breakpoint = "macro-studio:breakpoint";
  const styleBreakpoint = block => block.getSvgRoot()?.classList.toggle("msBreakpoint", block.data === breakpoint);
  Blockly.ContextMenuRegistry.registry.register({
    id: "macro-studio-toggle-breakpoint",
    scopeType: Blockly.ContextMenuRegistry.ScopeType.BLOCK,
    weight: 20,
    displayText: ({block}) => block.data === breakpoint ? "브레이크포인트 제거" : "브레이크포인트 설정",
    preconditionFn: ({block}) => block.previousConnection?.getCheck() === null &&
      !block.isInFlyout && !block.isShadow() ? "enabled" : "hidden",
    callback: ({block}) => {
      block.data = block.data === breakpoint ? null : breakpoint;
      styleBreakpoint(block);
      scheduleWorkspacePublish();
    },
  });
  let viewing = false;
  let activeBlockRoots = new Set();
  function showActiveBlocks(ids) {
    const roots = new Set(ids.map(id => workspace.getBlockById(id)?.getSvgRoot()).filter(Boolean));
    for (const root of activeBlockRoots) {
      if (!roots.has(root)) root.classList.remove("msExecuting");
    }
    for (const root of roots) {
      if (!activeBlockRoots.has(root)) root.classList.add("msExecuting");
    }
    activeBlockRoots = roots;
  }
  bridge.activeBlocksChanged.connect(showActiveBlocks);
  bridge.loadWorkspace.connect(data => {
    viewing = true;
    try {
      Blockly.Events.disable();
      showActiveBlocks([]);
      const state = JSON.parse(data);
      state.blocks?.blocks?.forEach(upgradeBlockInputs);
      Blockly.serialization.workspaces.load(state, workspace);
      workspace.getAllBlocks(false).forEach(refreshMotionProfile);
      syncCallArguments(workflowSignatures());
      workspace.getAllBlocks(false).forEach(styleBreakpoint);
      workspace.clearUndo();
      if (workspace.getTopBlocks(false).length) {
        workspace.zoomToFit();
        if (workspace.scale > 0.92) workspace.setScale(0.92);
        workspace.scrollCenter();
      }
    } finally {
      Blockly.Events.enable();
      viewing = false;
    }
    refreshWorkflowCategory(workflowSignatures());
  });
  let lastWidth = document.documentElement.clientWidth;
  window.addEventListener("resize", () => {
    const width = document.documentElement.clientWidth;
    if (width < lastWidth && workspace.getTopBlocks(false).length) {
      Blockly.svgResize(workspace);
      const scale = workspace.scale;
      workspace.zoomToFit();
      if (workspace.scale > scale) workspace.setScale(scale);
      workspace.scrollCenter();
    }
    lastWidth = width;
  });
  bridge.focusBlock.connect(id => {
    workspace.highlightBlock(id || null);
    if (id && workspace.getBlockById(id)) workspace.centerOnBlock(id);
  });
  bridge.navigateBlock.connect(id => {
    const block = workspace.getBlockById(id);
    if (!block) return;
    const parents = [];
    for (let parent = block.getSurroundParent(); parent; parent = parent.getSurroundParent()) parents.unshift(parent);
    for (const parent of parents) {
      if (parent.isCollapsed()) parent.setCollapsed(false);
    }
    workspace.highlightBlock(id);
    workspace.centerOnBlock(id);
  });
  workspace.addChangeListener(event => {
    if (!viewing && event.type === Blockly.Events.BLOCK_DRAG && !event.isStart) scheduleWorkspacePublish();
    if (!viewing && !event.isUiEvent) {
      const block = workspace.getBlockById(event.blockId);
      if (event.type === Blockly.Events.BLOCK_CREATE || event.type === Blockly.Events.BLOCK_DELETE ||
          event.type === Blockly.Events.BLOCK_MOVE ||
          (event.type === Blockly.Events.BLOCK_CHANGE && event.element === "field" &&
           ((event.name === "NAME" && (block?.type === "ms_workflow" || callTypes.has(block?.type))) ||
            (block?.type === "ms_param_def" && (event.name === "NAME" || event.name === "TYPE"))))) {
        argumentSyncNeeded = true;
      }
      for (const id of [event.blockId, event.oldParentId, event.newParentId]) {
        if (id) {
          const root = workspace.getBlockById(id)?.getRootBlock();
          if (root) headersToRefresh.add(root);
        }
      }
      if (event.type === Blockly.Events.BLOCK_CREATE) {
        event.ids?.map(id => workspace.getBlockById(id)).filter(Boolean).forEach(styleBreakpoint);
        event.ids?.forEach(id => refreshMotionProfile(workspace.getBlockById(id)));
      }
      if (event.type === Blockly.Events.BLOCK_CHANGE && event.name === "PROFILE") refreshMotionProfile(block);
      scheduleWorkspacePublish();
    }
  });
  bridge.ready();
});
