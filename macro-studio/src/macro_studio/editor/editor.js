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
const workflowList = (name, check) => ({type: "input_statement", name, check});
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
const singleKeyBlocks = new Set(["ms_key_tap", "ms_tap_sequence"]);

class StudioKeyField extends Blockly.FieldTextInput {
  static fromJson(options) {
    return new StudioKeyField(options.text);
  }

  getText_() {
    return `${this.getValue()} · 키 듣기`;
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

Blockly.defineBlocksWithJsonArray([
  hat("start", "event", "%1 %2 우선순위 %3 조건 %4 %5",
    [label("TITLE", "프로젝트 시작"), policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  hat("hotkey", "event", "%1 %2 %3 우선순위 %4 조건 %5 %6",
    [label("TITLE", "단축키"), value("KEY", "Key"), policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  hat("interval", "event", "%1 %2 %3 우선순위 %4 조건 %5 %6",
    [number("MS", 1000, 1, 86400000, 1), label("TITLE", "ms마다"),
     policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  hat("custom", "event", "%1 %2 %3 %4 우선순위 %5 조건 %6 %7",
    [label("TITLE", "이벤트"), input("NAME", "준비 완료"), label("TITLE_END", "를 받으면"),
     policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  statement("emit_event", "event", "이벤트 %1 발행", [input("NAME", "준비 완료")]),
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
  statement("mouse_click_at", "input", "%1 를 %2 하기", [value("POSITION", "Position"),
    dropdown("BUTTON", [["좌클릭", "left"], ["우클릭", "right"], ["가운데 클릭", "middle"]])], {inputsInline: true}),
  statement("tap_sequence", "input", "%1 키 연타 %2 Hz · %3 · %4 회",
    [value("KEY", "Key"), value("HZ", "Number"), value("DURATION", "Duration"), value("COUNT", "Number")],
    {inputsInline: true}),
  statement("wait", "time", "%1 ms만큼 기다리기", [number("MS", 100, 0, 86400000, 0.001)]),
  statement("wait_time", "time", "%1 만큼 기다리기", [value("DURATION", "Duration")], {inputsInline: true}),
  {...expression("duration", "%1 %2", [value("VALUE", "Number"),
    dropdown("UNIT", [["sec", "sec"], ["ms", "ms"], ["min", "min"]])], "Duration"),
    colour: palette.time, inputsInline: true, tooltip: "숫자와 단위를 묶어 시간을 지정합니다."},
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
  statement("set", "variable", "변수 %1 에 %2 저장", [input("NAME", "enabled"), value("VALUE")]),
  expression("number", "%1", [number("VALUE", 1)], "Number", "arithmetic"),
  expression("boolean", "%1", [dropdown("VALUE", [["참", "TRUE"], ["거짓", "FALSE"]])], "Boolean", "logic"),
  expression("text", "문자열 %1", [input("VALUE", "")], "String", "variable"),
  expression("variable", "변수 %1", [input("NAME", "enabled")], null, "variable"),
  {...expression("binary", "%1 %2 %3", [value("A"),
    dropdown("OP", [...arithmeticOperators, ...comparisonOperators, ...logicalOperators]), value("B")], null),
    inputsInline: true},
  {...expression("arithmetic", "%1 %2 %3", [value("A", "Number"),
    dropdown("OP", arithmeticOperators), value("B", "Number")], "Number", "arithmetic"), inputsInline: true},
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
  {...expression("region_image", "영역 %1 너비 %2 높이 %3 에 이미지 %4 있음",
    [value("POSITION", "Position"), value("WIDTH", "Number"), value("HEIGHT", "Number"),
     {type: "field_studio_image_asset", name: "IMAGE"}], "Boolean"), colour: "#23627c", inputsInline: true},
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
    {message1: "인자 %1", args1: [workflowList("ARGS", "ArgumentList")]}),
  statement("call_store", "workflow", "워크플로 %1 결과를 변수 %2 에 저장",
    [input("NAME", "작업"), input("RESULT", "결과")],
    {message1: "인자 %1", args1: [workflowList("ARGS", "ArgumentList")]}),
  statement("call_if", "workflow", "워크플로 %1 결과가 참이면", [input("NAME", "작업")],
    {message1: "인자 %1", args1: [workflowList("ARGS", "ArgumentList")],
     message2: "실행 %1 그 외 %2", args2: [stack("THEN"), stack("ELSE")]}),
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
  ["마우스", "input", ["position", "mouse_move", "mouse_click_at", "mouse_click"]],
  ["시간", "time", ["duration", "wait_time"]],
  ["제어", "control", ["repeat", "forever", "while", "if_only", "if", "break", "continue", "end"]],
  ["변수", "variable", ["set", "variable", "text"]],
  ["산술 연산", "arithmetic", ["number", "arithmetic"]],
  ["논리 연산", "logic", ["boolean", "comparison", "logical", "not"]],
  ["화면 센서", "screen", ["pixel_color", "region_image", "region_text", "target_window"]],
  ["워크플로", "workflow", ["workflow", "param_def", "parameter", "argument", "call", "call_store", "call_if", "return"]],
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
  position: positionInputs,
  wait_time: {DURATION: defaultDuration},
  mouse_move: {POSITION: defaultPosition, DURATION: defaultDuration},
  mouse_click_at: {POSITION: defaultPosition},
  pixel_color: {POSITION: defaultPosition},
  region_image: defaultRegion,
  region_text: defaultRegion,
  repeat: {COUNT: numberShadow(1)},
  while: inactiveCondition,
  if_only: inactiveCondition,
  if: inactiveCondition,
  arithmetic: {A: numberShadow(0), B: numberShadow(1)},
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
  ms_hotkey: ["TITLE"],
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
    return block.isCollapsed() ? [headerFields[block.type].map(field).join(" ")] : [];
  }
  const condition = block.getInputTargetBlock("GUARD")?.toString(120);
  const guard = condition ? "조건 · " + condition : "조건 없음";
  switch (block.type) {
    case "ms_state": return ["상태 · " + field("NAME")];
    case "ms_state_condition":
      return [field("FROM") + " → " + field("TO"), condition ? guard : "조건 연결 필요"];
    case "ms_state_hotkey": return [field("FROM") + " → " + field("TO"),
      (block.getInputTargetBlock("KEY")?.getFieldValue("VALUE") || "키 선택") + (condition ? " · " + condition : "")];
    default: return [];
  }
}

function updateBlockHeader(block) {
  if (!block || block.previousConnection || block.outputConnection || !block.getSvgRoot()) return;
  if (block.type === "ms_hotkey") {
    const title = block.getField("TITLE");
    const key = block.getInputTargetBlock("KEY")?.getFieldValue("VALUE");
    const text = key ? `단축키 ${key}` : "단축키";
    if (title.getValue() !== text) {
      Blockly.Events.disable();
      try {
        title.setValue(text);
      } finally {
        Blockly.Events.enable();
      }
    }
  }
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
    const fields = names.map(name => this.block_.getField(name));
    this.headerFields = fields.map(field => ({field, size: field.getSize()}));
    this.headerContentHeight = Math.max(...this.headerFields.map(({size}) => size.height));
    const contentWidth = this.headerFields.reduce((sum, {size}) => sum + size.width, 0)
      + headerFieldGap * (fields.length - 1);
    this.headerShape = headerShape(contentWidth + 2 * headerPadding, this.headerContentHeight + 2 * headerPadding);
    Object.assign(hat, {width: this.headerShape.width, height: this.headerShape.height,
      ascenderHeight: this.headerShape.height});
    for (const row of this.inputRows) {
      row.elements = row.elements.filter(element => !fields.includes(element.field));
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

  draw() {
    super.draw();
    if (!this.info_.headerFields) return;
    let x = headerPadding;
    for (const {field, size} of this.info_.headerFields) {
      const y = headerPadding + (this.info_.headerContentHeight - size.height) / 2;
      const root = field.getSvgRoot();
      root.setAttribute("transform", `translate(${x},${y})`);
      if (this.info_.isInsertionMarker) root.setAttribute("display", "none");
      x += size.width + headerFieldGap;
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
let shownWorkflowNames = [];

function projectWorkflowNames() {
  const names = workspace.getTopBlocks(false)
    .filter(block => block.type === "ms_workflow")
    .map(block => block.getFieldValue("NAME"))
    .filter(name => name.trim() && !name.startsWith("@"));
  return [...new Set(names)].sort((first, second) => first.localeCompare(second, "ko"));
}

workspace.registerToolboxCategoryCallback(workflowCategory, () => {
  shownWorkflowNames = projectWorkflowNames();
  const projectBlocks = shownWorkflowNames.map(NAME => ({...toolboxBlock("call"), fields: {NAME}}));
  return [
    {kind: "label", text: projectBlocks.length ? "프로젝트 워크플로" : "정의한 워크플로 없음"},
    ...projectBlocks,
    {kind: "label", text: "워크플로 도구"},
    ...workflowTools.map(toolboxBlock),
  ];
});

function refreshWorkflowCategory() {
  const toolbox = workspace.getToolbox();
  if (toolbox.getSelectedItem()?.getId() !== workflowCategory || workspace.isDragging()) return;
  const names = projectWorkflowNames();
  if (names.length !== shownWorkflowNames.length || names.some((name, index) => name !== shownWorkflowNames[index])) {
    toolbox.refreshSelection();
  }
}

function upgradeBlockInputs(block) {
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
  const headersToRefresh = new Set();
  function scheduleWorkspacePublish() {
    if (workspacePublishQueued) return;
    workspacePublishQueued = true;
    queueMicrotask(() => {
      workspacePublishQueued = false;
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
  bridge.loadWorkspace.connect(data => {
    viewing = true;
    try {
      Blockly.Events.disable();
      const state = JSON.parse(data);
      state.blocks?.blocks?.forEach(upgradeBlockInputs);
      Blockly.serialization.workspaces.load(state, workspace);
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
    refreshWorkflowCategory();
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
  workspace.addChangeListener(event => {
    if (!viewing && (event.type === Blockly.Events.BLOCK_CREATE || event.type === Blockly.Events.BLOCK_DELETE ||
        (event.type === Blockly.Events.BLOCK_CHANGE && event.element === "field" && event.name === "NAME") ||
        (event.type === Blockly.Events.BLOCK_DRAG && !event.isStart))) {
      refreshWorkflowCategory();
    }
    if (!viewing && !event.isUiEvent) {
      for (const id of [event.blockId, event.oldParentId, event.newParentId]) {
        if (id) {
          const root = workspace.getBlockById(id)?.getRootBlock();
          if (root) headersToRefresh.add(root);
        }
      }
      if (event.type === Blockly.Events.BLOCK_CREATE) {
        event.ids?.map(id => workspace.getBlockById(id)).filter(Boolean).forEach(styleBreakpoint);
      }
      scheduleWorkspacePublish();
    }
  });
  bridge.ready();
});
