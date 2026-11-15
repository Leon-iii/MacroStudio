"use strict";

const palette = {
  event: "#9a4a0e", input: "#2459a6", time: "#20646d", control: "#356843",
  data: "#70458c", workflow: "#59607b", state: "#744756",
};
const input = (name, text) => ({type: "field_input", name, text});
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
const coordinate = name => number(name, 0, -32768, 32767, 1);
const valueTypes = [["숫자", "number"], ["불리언", "boolean"], ["문자열", "string"]];
const workflowList = (name, check) => ({type: "input_statement", name, check});
const statement = (type, category, message0, args0 = [], extra = {}) => ({
  type: "ms_" + type, colour: palette[category], message0, args0,
  tooltip: message0.replace(/%\d+/g, "").trim(), previousStatement: null, nextStatement: null, ...extra,
});
const expression = (type, message0, args0, output) => ({
  type: "ms_" + type, colour: palette.data, message0, args0, output,
  tooltip: message0.replace(/%\d+/g, "").trim(),
});
const hat = (type, category, message0, args0) => ({
  type: "ms_" + type, colour: palette[category], message0, args0, hat: "cap",
  tooltip: message0.replace(/%\d+/g, "").trim(),
});
const operators = [
  ["+", "add"], ["−", "sub"], ["×", "mul"], ["÷", "div"], ["=", "eq"], ["≠", "ne"],
  ["<", "lt"], ["≤", "le"], [">", "gt"], ["≥", "ge"], ["그리고", "and"], ["또는", "or"],
];
Blockly.defineBlocksWithJsonArray([
  hat("start", "event", "프로젝트 시작 %1 우선순위 %2 조건 %3 실행 %4",
    [policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  hat("hotkey", "event", "단축키 %1 %2 우선순위 %3 조건 %4 실행 %5",
    [input("KEY", "CTRL+F7"), policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  hat("interval", "event", "%1 ms마다 %2 우선순위 %3 조건 %4 실행 %5",
    [number("MS", 1000, 1, 86400000, 1), policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  hat("custom", "event", "이벤트 %1 을 받으면 %2 우선순위 %3 조건 %4 실행 %5",
    [input("NAME", "준비 완료"), policy(), priority(), value("GUARD", "Boolean"), stack("BODY")]),
  statement("emit_event", "event", "이벤트 %1 발행", [input("NAME", "준비 완료")]),
  statement("key_tap", "input", "%1 키를 %2 ms 누르기", [input("KEY", "A"), number("MS", 50, 0.001, 86400000, 0.001)]),
  statement("mouse_click", "input", "마우스 %1 버튼을 %2 ms 누르기",
    [dropdown("BUTTON", [["왼쪽", "left"], ["오른쪽", "right"], ["가운데", "middle"]]),
     number("MS", 50, 0.001, 86400000, 0.001)]),
  statement("tap_sequence", "input", "%1 키 연타 %2 Hz · %3 ms · %4 회",
    [input("KEY", "A"), number("HZ", 100, 1, 200, 1), number("MS", 2, 0.001, 999, 0.001), number("COUNT", 10, 1, 1000000, 1)]),
  statement("wait", "time", "%1 ms 기다리기", [number("MS", 100, 0, 86400000, 0.001)]),
  statement("repeat", "control", "%1 회 반복 %2", [value("COUNT", "Number"), stack("BODY")]),
  statement("forever", "control", "계속 반복 %1", [stack("BODY")]),
  statement("if", "control", "만약 %1 실행 %2 그 외 %3", [value("CONDITION", "Boolean"), stack("THEN"), stack("ELSE")]),
  statement("end", "control", "이 작업 종료", [], {nextStatement: undefined}),
  statement("set", "data", "변수 %1 에 %2 저장", [input("NAME", "enabled"), value("VALUE")]),
  expression("number", "%1", [number("VALUE", 1)], "Number"),
  expression("boolean", "%1", [dropdown("VALUE", [["참", "TRUE"], ["거짓", "FALSE"]])], "Boolean"),
  expression("text", "문자열 %1", [input("VALUE", "")], "String"),
  expression("variable", "변수 %1", [input("NAME", "enabled")], null),
  expression("binary", "%1 %2 %3", [value("A"), dropdown("OP", operators), value("B")], null),
  expression("not", "%1 의 반대", [value("VALUE", "Boolean")], "Boolean"),
  {...expression("pixel_color", "픽셀 X %1 Y %2 색상 %3 일치",
    [coordinate("X"), coordinate("Y"), input("COLOR", "#ffffff")], "Boolean"), colour: "#23627c"},
  {...expression("target_window", "대상 창 상태가 %1",
    [dropdown("STATE", [["전면", "foreground"], ["열려 있음", "exists"], ["최소화", "minimized"]])],
    "Boolean"), colour: "#23627c"},
  {...expression("region_image", "영역 X %1 Y %2 너비 %3 높이 %4 에 이미지 %5 있음",
    [coordinate("X"), coordinate("Y"), number("WIDTH", 320, 1, 1024, 1),
     number("HEIGHT", 240, 1, 1024, 1), input("IMAGE", "template.png")], "Boolean"), colour: "#23627c"},
  {...expression("region_text", "영역 X %1 Y %2 너비 %3 높이 %4 에 텍스트 %5 있음 %6",
    [coordinate("X"), coordinate("Y"), number("WIDTH", 320, 1, 1024, 1),
     number("HEIGHT", 240, 1, 1024, 1), input("TEXT", "READY"),
     dropdown("LANGUAGE", [["영어", "eng"], ["한국어", "kor"], ["한국어+영어", "kor+eng"]])],
    "Boolean"), colour: "#23627c"},
  hat("workflow", "workflow", "워크플로 %1 반환 타입 %2 매개변수 %3 실행 %4",
    [input("NAME", "작업"), dropdown("RETURNS", [["없음", "void"], ...valueTypes]),
     workflowList("PARAMS", "ParameterList"), stack("BODY")]),
  statement("param_def", "workflow", "매개변수 %1 타입 %2",
    [input("NAME", "값"), dropdown("TYPE", valueTypes)],
    {previousStatement: "ParameterList", nextStatement: "ParameterList"}),
  expression("parameter", "매개변수 %1 값", [input("NAME", "값")], null),
  statement("argument", "workflow", "전달할 값 %1", [value("VALUE")],
    {previousStatement: "ArgumentList", nextStatement: "ArgumentList"}),
  statement("call", "workflow", "워크플로 %1 호출 인자 %2",
    [input("NAME", "작업"), workflowList("ARGS", "ArgumentList")]),
  statement("call_store", "workflow", "워크플로 %1 결과를 변수 %2 에 저장 인자 %3",
    [input("NAME", "작업"), input("RESULT", "결과"), workflowList("ARGS", "ArgumentList")]),
  statement("call_if", "workflow", "워크플로 %1 결과가 참이면 인자 %2 실행 %3 그 외 %4",
    [input("NAME", "작업"), workflowList("ARGS", "ArgumentList"), stack("THEN"), stack("ELSE")]),
  statement("return", "workflow", "%1 반환", [value("VALUE")], {nextStatement: undefined}),
  hat("state", "state", "상태 %1 시작 상태 %2 진입 워크플로 %3",
    [input("NAME", "대기"), dropdown("INITIAL", [["아니요", "FALSE"], ["예", "TRUE"]]), input("WORKFLOW", "")]),
  hat("state_condition", "state", "전이 %1 에서 %2 로 조건 %3",
    [input("FROM", "대기"), input("TO", "실행"), value("GUARD", "Boolean")]),
  hat("state_hotkey", "state", "전이 %1 에서 %2 로 핫키 %3 추가 조건 %4",
    [input("FROM", "실행"), input("TO", "대기"), input("KEY", "CTRL+F7"), value("GUARD", "Boolean")]),
]);

const categories = [
  ["이벤트", "event", ["start", "hotkey", "interval", "custom", "emit_event"]],
  ["키보드", "input", ["key_tap", "tap_sequence"]],
  ["마우스", "input", ["mouse_click"]],
  ["시간", "time", ["wait"]],
  ["제어", "control", ["repeat", "forever", "if", "end"]],
  ["변수·연산", "data", ["set", "variable", "number", "boolean", "text", "binary", "not"]],
  ["화면 센서", "screen", ["pixel_color", "region_image", "region_text", "target_window"]],
  ["워크플로", "workflow", ["workflow", "param_def", "parameter", "argument", "call", "call_store", "call_if", "return"]],
  ["상태 머신", "state", ["state", "state_condition", "state_hotkey"]],
];
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
const workspace = Blockly.inject("workspace", {
  toolbox: {kind: "categoryToolbox", contents: categories.map(([name, color, blocks]) => ({
    kind: "category", name, colour: color === "screen" ? "#23627c" : palette[color],
    contents: blocks.map(type => ({kind: "block", type: "ms_" + type})),
  }))},
  theme, renderer: "zelos", media: "./media/",
  grid: {spacing: 24, length: 2, colour: "#b9c4d2", snap: true},
  zoom: {controls: true, wheel: false, startScale: 0.92, minScale: 0.45, maxScale: 1.5},
  move: {scrollbars: true, drag: true, wheel: true},
});

new QWebChannel(qt.webChannelTransport, channel => {
  const bridge = channel.objects.bridge;
  let workspacePublishQueued = false;
  function scheduleWorkspacePublish() {
    if (workspacePublishQueued) return;
    workspacePublishQueued = true;
    queueMicrotask(() => {
      workspacePublishQueued = false;
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
      Blockly.serialization.workspaces.load(JSON.parse(data), workspace);
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
    if (!viewing && !event.isUiEvent) {
      if (event.type === Blockly.Events.BLOCK_CREATE) {
        event.ids?.map(id => workspace.getBlockById(id)).filter(Boolean).forEach(styleBreakpoint);
      }
      scheduleWorkspacePublish();
    }
  });
  bridge.ready();
});
