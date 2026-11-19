# Macro Studio v1.4 — Python Scripting & AI Code Generation

> **문서 유형:** Codex 구현용 규범적 확장 명세서 / 기존 v1.3 + R-0~R-6 이후 로드맵
>
> **문서 버전:** 1.4.0 (기획 / 구현 명세)
>
> **작성일:** 2026-11-17
>
> **대상:** Windows 11 x64, Python 3.12+, PySide6 / Blockly, 기존 Macro Studio Runner·IR·Output Router·Perception 서비스
>
> **기준 문서:** `MacroStudio_Codex_Implementation_Spec_v1_3_Integrated.md`, `MacroStudio_Visual_Recognition_Spec_v1_3.md`, 기존 v1.0~v1.2 명세, 범용 서비스 단계 R-0~R-6 설계
>
> **한 문장 목표:** 사용자가 **블록 또는 제한된 Python DSL**로 동일한 자동화 워크플로를 작성하고, 외부 LLM이 생성한 Python도 **정적 검증 → 공통 IR 컴파일 → 미리보기/Dry Run → 명시적 실행** 경로로 안전하고 일관되게 사용할 수 있게 한다.

---

## PY-0. Codex 최상위 작업 지시 및 버전 충돌 정리

1. 저장소의 실제 구현과 테스트 상태를 **먼저** 조사한다. 명세에 적힌 모듈이 이미 존재한다고 가정하지 않는다.
2. v1.0~v1.3의 Blockly, IR, Runner, 비차단 Task Scheduler, Input Arbiter, Mouse Motion Profile, Perception Worker 및 SendInput/External HID 백엔드를 보존한다. 정상 동작하는 코드를 전면 재작성하지 않는다.
3. 이 문서는 이전 명세에 후속으로 적용하는 **Part E: Python Scripting & AI Code Generation**이다. Python 편집·컴파일·모드 변환에 대해서는 이 문서를 우선한다. 화면 인식은 v1.3 Part D, 마우스 모션은 v1.2 Part C, 멀티 출력은 v1.1, 공통 런타임 안전성은 v1.0의 규칙을 유지한다.
4. 과거 Codex 작성 문서 `MacroStudio_Rune_Recognition_Spec_v1_4.md`의 **파일명에 포함된 v1.4는 충돌하는 임시 명명**이다. 내용은 Auto Maple 전용 **Rune Recognition Extension**이며, 이 문서가 정의하는 제품 v1.4의 범위/버전으로 해석하지 않는다. 기존 파일을 무조건 삭제·자동 개명하지 말고, `docs/VERSIONING.md`에 별도 확장으로 표시한다.
5. 이전에 합의한 R-0~R-6 범용 서비스 아키텍처가 미완성이면 그 의존성과 미구현 기능을 명시하고, 최소 인터페이스·테스트 더블로 Python 기능을 독립 개발할 수 있게 한다. **R-0~R-6가 완료되었다고 가정하지 않는다.**
6. `docs/PYTHON_V1_4_GAP.md`에 기존 구현과 본 문서의 차이, 선행 의존성, 불명확한 API/IR 이름, 단계별 작업을 기록한다.
7. 이 문서에서 제시하는 Python/JSON/API 이름은 **규범적 의미론을 설명하는 제안**이다. 실제 저장소에 같은 기능의 안정된 계약이 있으면 매핑 테이블을 만들고 기존 이름을 우선 사용한다. 동작 의미론은 유지한다.
8. **매 작업 요청에서는 첫 번째 미완료 PY 단계 하나만 구현**한다. 요구사항·테스트·마이그레이션을 단계별로 완료하고 보고한다.
9. Codex는 실행 테스트에서 기본적으로 Fake/Dry Run 백엔드를 사용한다. **실제 키보드·마우스 입력은 명시적 ARM, 포커스 및 안전 정책 검증 후에만** 가능하다.
10. 코드와 문서는 기본적으로 한국어 UI 및 문서, 내부 ID·IR opcode는 영어로 작성한다.

### v1.4 범위가 아닌 것

- 게임별 규칙, Maple 전용 명령, 룬 모델 학습·재배포 및 게임별 자동화 로직의 Core 편입
- 임의의 Python 파일을 `exec`/`eval`/`subprocess`로 실행하는 기능
- 일반 Python 전체 문법 및 모든 PyPI 패키지 지원
- 기본 앱에 외부 LLM API 키 저장이나 내장 AI 채팅 서비스 구현
- 블록으로 표현 불가능한 임의 Python 소스의 무손실 양방향 변환
- 자동으로 생성 코드를 즉시 ARM하여 실행하는 기능
- 안티치트·보호된 화면·게임 보안 우회 및 숨겨진 입력 주입

---

## E1. 제품 목표, 핵심 사용자 경험

### E1.1 사용자 유형

- **블록 사용자:** 기존 Blockly UI로 자동화 프로그램을 구성하며 Python 코드를 확인하거나 편집하고 싶다.
- **Python 사용자:** 제한된 고수준 Python 문법으로 핫키·반복·인식·마우스·출력 서비스를 제어한다.
- **LLM 사용자:** 외부 LLM/Codex가 생성한 Macro Studio용 코드를 붙여 넣고 검증·블록 전환·Dry Run으로 확인한다.
- **서비스/플러그인 작성자:** 범용 서비스 및 Application Pack의 안전하게 노출 가능한 호출을 선언적으로 등록하여 두 편집 모드에서 사용하게 한다.

### E1.2 반드시 성립할 사용자 여정

1. 기존 블록 프로젝트를 열고 `Python` 탭으로 전환한다. 지원되는 블록의 의미가 보존된 Python 코드가 표시된다.
2. Python 탭에서 핫키와 입력/인식 명령을 수정한다. 오류 없는 코드는 IR로 검증되고, `블록` 탭으로 돌아가면 같은 의미의 블록이 재생성된다.
3. 외부 LLM이 생성한 Python을 별도 편집기에 붙여 넣는다. 자동 실행하지 않고 컴파일 진단·사용 서비스·필요 권한을 보여준다.
4. 사용자는 Dry Run 기록/이벤트 타임라인을 확인한 뒤에만 실제 실행을 명시적으로 ARM한다.
5. 동일한 Python/블록 프로그램을 실행 시 **SendInput / External HID** 중 하나로 선택한다. 출력 백엔드 변경을 위해 코드를 수정할 필요가 없다. 미지원 명령은 실행 전 명확히 안내한다.
6. Auto Maple 없이도 똑같은 Python DSL과 서비스 API가 사용 가능하다. 특화 동작은 Application Pack이 이름 있는 서비스/액션으로 제공한다.

### E1.3 수용 범위 우선순위

| 영역 | v1.4 필수 | 후속 |
|---|---|---|
| 편집 모드 | Blockly ↔ 표준 제한 Python DSL | 일반 Python 확장 |
| 변환 | 지원 문법 내 의미 보존 왕복 | 임의 Python을 블록으로 자동 복구 |
| 코드 생성 | Blockly → 정형 Python | 자연어 → 내장 LLM 자동 생성 |
| 역방향 | Python AST → IR/블록 | Python 모듈 동적 로딩 |
| 검증 | 구문·타입·API·Capability·자원 제한 | 정교한 정적 분석/보안 검증 확대 |
| 실행 | **기존 IR Runner만 사용** | 별도 격리 Advanced Python Worker |
| LLM 준비 | API 문서, 예제, 프롬프트 계약, 오류 피드백 | 모델 제공자 연동·키 관리 |
| 서비스 | Keyboard/Mouse/Vision/Workflow + 등록된 범용 서비스 | 서드파티 사용자 Python 실행 플러그인 |

---

## E2. 변경 후 아키텍처

```text
                         ┌──────────────────────────────┐
                         │ PySide6 / Macro Studio GUI   │
                         │ [블록] [Python] [검증] [실행] │
                         └──────────┬───────────┬───────┘
                                    │           │
                           Blockly Workspace   Python Source
                                    │           │
                            Block Compiler   Python AST Compiler
                                    │           │
                                    └─────┬─────┘
                                          ▼
                              Common Validated IR
                                          │
                           Compiler Diagnostics / Source Map
                                          │
                            IR Validator + Capability Check
                                          │
                   ┌──────────────────────┴──────────────────┐
                   ▼                                         ▼
              Fake/Dry Run                          Existing Runner
                   │                          Task / Perception / Scheduler
                   │                                         │
                   └────── Telemetry / Preview ──────────────┤
                                                             ▼
                                                   Output Router
                                                SendInput / External HID
```

**하드 규칙:** Python 소스는 편집·생성·검증용 언어다. **실제 실행 바이트코드는 공통 IR**이며, Script 실행을 위해 두 번째 병렬 Python Runtime을 만들지 않는다.

### E2.1 컴포넌트 역할

| 컴포넌트 | 책임 | 금지 사항 |
|---|---|---|
| Python Editor | 텍스트 입력, 구문 강조, 진단 표시, 변환 명령 | 실행 API 직접 호출 |
| Python AST Parser | `ast.parse`로 구문 구조화 | `exec`, `eval`, 동적 import |
| Semantic Analyzer | 이름·타입·허용 문법·서비스 계약 확인 | 알 수 없는 표현을 관대하게 실행 |
| IR Lowerer | 검증된 Python을 기존 IR 명령으로 변환 | 자체 별도 Input backend 추가 |
| Python Code Generator | IR/ProgramModel → 정형 Python | IR에 없는 동작 임의 생성 |
| Block Renderer | 지원 IR → Blockly 구성 | 지원 불가능 부분 몰래 삭제 |
| Service API Registry | 컴파일에 사용 가능한 API/타입/Capabilities 제공 | Application Pack 코드 자동 실행 |
| LLM Authoring Pack | 생성 모델용 명세/예제/검증 오류 출력 | 생성 코드 자동 ARM/실행 |
| Existing Runner | 검증된 IR만 실행 | 사용자 Python 직접 실행 |

### E2.2 계층 의존성

```text
applications/* (도메인 Application Pack)
                ↓
reusable services / service manifests
                ↓
Python DSL type/API descriptors & IR lowering adapters
                ↓
core contracts, IR, scheduler, output/observation interfaces
```

- `core`, `python_dsl`, `compiler`는 `applications/auto_maple`을 import하지 않는다.
- Auto Maple 전용 `job`, `rune`, `minimap`은 v1.4 표준 라이브러리의 고정 키워드가 아니다.
- 등록된 선택 서비스를 호출할 때는 **확정된 서비스 ID/버전/Capability**를 사용한다.
- Python 편집 기능은 화면 캡처, ML 모델 및 External HID 장치가 없어도 시작·검증이 가능해야 한다.

---

## E3. 프로젝트 저장/모드 전환 모델

### E3.1 구분할 세 가지 표현

1. **편집 표현:** Blockly Workspace JSON 또는 Python 소스 코드. 사용자 작성의 원본을 보존한다.
2. **공통 ProgramModel/IR:** 핸들러, 워크플로, 명령, 타입을 포함한 의미 구조. v1.3의 기존 실행 IR 스키마를 최대한 재사용한다.
3. **실행 스냅샷:** 현재 편집본에서 컴파일되어 검증된 불변 IR, 프로파일/템플릿/서비스 버전 스냅샷, Capability 검사 결과.

블록 좌표, 주석, Python 공백, 표현식의 구체적 표기 같은 편집 메타데이터는 실행 IR의 일부가 아니다. 동등한 프로그램이더라도 변환 후 Python 서식이나 블록 배치가 달라질 수 있다. **보존 대상은 지원 문법 내 실행 의미론과 안정적인 진단 위치다.**

### E3.2 세션 소스 오브 트루스

- **활성 편집 모드의 수정 중인 소스**가 편집 기준이다.
- `검증` 시에만 해당 소스를 IR로 컴파일한다. 실행은 반드시 가장 최근에 **현재 소스와 fingerprint가 같은 유효한 IR**을 사용한다.
- Python에 구문 오류가 있어도 저장할 수 있는 초안(Draft)은 허용하되, 이 상태에서는 ARM/START를 비활성화한다. 이전 유효 IR을 **묵시적으로 실행하지 않는다**.
- 모드 전환은 `현재 편집 소스 → 검증된 IR → 목표 편집 표현` 순서로 실행한다.
- 변환 불가능한 노드가 있으면 이유·위치·지원 대안을 제시하고 전환을 거절한다. 조용히 누락시키지 않는다.
- 전환 전 현재 편집 내용을 자동 보존한다. 사용자가 동의하지 않는 이상 읽기 어려운 Python 코드를 블록 변환 명목으로 파괴적으로 덮어쓰지 않는다.
- 원본 Python의 주석과 서식은 코드 재생성 시 유지되지 않을 수 있으므로 명확히 안내한다. 사용자 초안은 별도 보존한다.
- 모드 전환 이후 사용자가 변경하지 않은 경우 편집기 간 순환 시 불필요한 코드 재생성을 방지할 수 있다.

### E3.3 편집 프로젝트 예시(기존 스키마에 증분 병합)

```json
{
  "project_schema_version": 4,
  "name": "Confirm workflow",
  "editor": {
    "active_mode": "python",
    "python": {
      "language": "macrostudio-python",
      "dsl_version": "1.4",
      "source": "@on_hotkey(\"F6\")\nasync def confirm():\n    result = await vision.find(\"template.confirm\")\n    if result.status == \"FOUND\":\n        await mouse.move_to(result.center_x, result.center_y, profile=\"builtin.natural\")\n        await mouse.click()\n"
    },
    "blockly_workspace": {"blocks": {"languageVersion": 0, "blocks": []}},
    "last_compiled_source_fingerprint": "example-placeholder"
  },
  "settings": {"dry_run_default": true, "focus_policy": "pause_on_blur"}
}
```

> `project_schema_version: 4`는 **예시**이며 실제 저장소의 다음 스키마 버전에 맞춰 결정한다. `last_compiled_source_fingerprint`는 저장 편의용이며 신뢰할 수 있는 권한 토큰이 아니다. 로드 시 스키마 검증과 컴파일을 다시 수행한다. 예시의 빈 Blockly Workspace는 실제 Python 소스와 동등한 변환 결과를 뜻하지 않는다.

### E3.4 구버전 호환

- v1.3 이하 프로젝트를 열면 기존 Blockly가 정상 표시되어야 한다.
- 사용자가 Python 편집을 선택하는 시점에만 Python 코드를 생성할 수 있다.
- 기존 JSON 프로젝트의 템플릿/모션 프로파일/출력 설정/핸들러 ID를 훼손하지 않는다.
- 후속 버전 프로젝트를 구버전으로 열 때는 안전하게 오류를 반환한다. 임의 필드 무시 후 실행하지 않는다.
- 마이그레이션은 원본 복사본을 보존하고, 재저장 전후의 IR 의미 동등성을 테스트한다.

---

## E4. Standard Python DSL (v1.4 정규 지원 범위)

### E4.1 언어 원칙

사용자가 보는 문법은 Python이지만, 내부적으로는 **Macro Studio가 지원하는 AST 부분집합**이다. 실제 CPython 스크립트 런타임과 1:1로 동일하다고 광고하지 않는다.

- 별도의 `import` 문 없이 DSL의 고정된 API 이름(`keyboard`, `mouse`, `vision`, `macro`, `workflow`, 등록 서비스 등)을 사용한다.
- 비차단 액션은 `await`로 표시한다. 컴파일러는 `await`를 비차단 IR/Task 예약으로 낮춘다. 실제 Python `asyncio` 코루틴을 실행하는 것이 아니다.
- `@on_start`, `@on_hotkey(...)`, `@on_interval(...)`, `@workflow(...)` 등 데코레이터는 **선언적 핸들러/워크플로 등록 구문**이다. 장식된 함수 객체를 실행하지 않는다.
- 함수 시그니처, API 함수, 키워드 인자, 결과 필드 접근은 Registry의 정적 계약에 정확히 일치해야 한다.
- 모든 반복과 대기에는 상한과 취소 지점이 존재해야 한다.

### E4.2 Python AST 지원 표

| 문법 | v1.4 | 의미 및 제한 |
|---|---|---|
| `async def name():` | 필수 | 최상위 핸들러 및 워크플로 선언; 사용자 함수의 임의 동적 실행 금지 |
| 허용된 데코레이터 | 필수 | 상수 인자/등록 타입만 지원 |
| `await known_api(...)` | 필수 | 등록된 서비스만 호출; 위치·키워드 인자 타입 검사 |
| `x = expr`, `x += expr` | 필수 | 로컬/전역 상태 범위에 맞춰 IR로 변환 |
| `if/elif/else` | 필수 | bool 타입 조건 / 제한된 비교 표현식 |
| `for i in range(n)` | 필수 | 정수 범위, 스텝 및 반복 상한 검증 |
| `while condition` | 필수(제약) | 루프에 yield/wait 또는 작업 예산에 따른 공정한 양보가 있어야 함; 무한 빠른 루프 거부 |
| `break`, `continue` | 필수 | 허용된 반복문 내에서만 |
| 산술·비교·논리 연산 | 필수 | 안전한 내장 primitive 및 정형 필드 접근만 |
| 문자열·정수·실수·bool·`None` | 필수 | 크기·범위 상한 적용 |
| 결과 객체의 필드 조회 | 필수 | 타입 검사된 공개 필드만 허용 |
| `return` | 제한 지원 | 워크플로 종료, 필요한 경우 타입 있는 반환값; 지원 범위 명시 |
| 임의 `def`, 람다, 클래스 | 제외 | 필요시 서비스 Registry 또는 IR 확장으로 추가 |
| `import`, `from`, `__import__` | 제외 | 직접 임의 모듈 접근 불가 |
| `exec`, `eval`, `compile`, `open`, `input` | 제외 | 동적 코드/파일/콘솔 접근 불가 |
| `getattr`, `setattr`, reflection, `__dunder__` | 제외 | 임의 속성/내부 객체 접근 불가 |
| `try/except/finally`, `with` | 제외 | v1.4는 결과 상태/구조화 오류 처리로 대체 |
| comprehension, generator, `yield` | 제외 | 정규 문법 밖 |
| 사용자 정의 import/패키지 | 제외 | 후속 Advanced Python 분리 |
| `asyncio`/`time.sleep` | 제외 | 반드시 `await macro.sleep(ms=...)` 사용 |

반복문은 컴파일 단계에 정적 상한 검사를 적용하고, 변수 기반 유한 루프는 런타임 횟수/명령 예산으로 제한한다. `while True`는 양보 지점이 있고 STOP에 반응하는 경우에 한해 허용할 수 있으나, 무제한 CPU 스핀은 거부한다. 실제 제한값은 기존 Runner 성능과 정책을 조사하여 상수로 정의하고 테스트한다.

### E4.3 이벤트/워크플로 선언

```python
@on_start
async def initialize():
    await macro.sleep(ms=100)

@on_hotkey("F6", reentrancy="ignore_if_running")
async def confirm_action():
    await workflow.call("click_confirm")

@on_interval(ms=1000)
async def periodic_check():
    await actions.run("status_check")

@workflow("click_confirm")
async def click_confirm():
    target = await vision.find(
        template_id="template.confirm",
        timeout_ms=1000,
    )
    if target.status == "FOUND":
        await mouse.move_to(
            x=target.center_x,
            y=target.center_y,
            profile_id="builtin.natural",
            duration_ms=300,
        )
        await mouse.click(button="left")
```

- 데코레이터나 호출 이름은 기존 저장소 명명과 충돌 시 매핑 가능하지만, 의미는 고정한다.
- `@on_start`는 매크로 프로젝트 시작 이벤트, `@on_hotkey`는 전역 핫키 이벤트, `@on_interval`은 주기 이벤트를 생성한다.
- `@workflow` 함수는 다른 핸들러/워크플로에서 호출하는 재사용 절차다.
- 동일한 핸들러/워크플로 식별자는 중복 선언할 수 없다.
- 함수 몸체의 상단에는 런타임에서 허용하는 단순 지역 변수만 정의할 수 있다.
- 이미지 이벤트는 v1.3의 등록된 이벤트가 지원할 때 `@on_image_appeared(template_id="...")` 등으로 확장한다. 존재하지 않는 기능을 무조건 생성하지 않는다.

### E4.4 표현식·타입

| 타입 | 예시 | 유의사항 |
|---|---|---|
| `Bool` | `True`, `result.status == "FOUND"` | 임의 객체의 암묵적 truthiness 금지 |
| `Int` / `Float` | `10`, `0.75` | 유한 수, NaN/Infinity 금지, 범위 검사 |
| `String` | `"F6"`, `"template.confirm"` | 길이 상한, ID 유효성 |
| `Position` | 결과의 중심 좌표 또는 공개 좌표 값 | 좌표 공간 식별 및 최신성 유지 |
| `RecognitionResult` | `await vision.find(...)` | `FOUND/NOT_FOUND/UNKNOWN` 삼상태 |
| `ServiceResult[T]` | 이동 성공·취소·실패 | 상태/오류 정보 포함 |
| `List[T]` | 후속/제한적 | 동적 리스트·임의 인덱싱 v1.4 정규 범위 밖 |

**중요:** `UNKNOWN`은 `NOT_FOUND`와 동의어가 아니다. 인식 오류/타임아웃/만료를 `False`로 뭉개면 안 된다. `result.status`에 따른 분기를 권장하고, `FOUND`가 아닌 상태에서 유효하지 않은 좌표에 접근하면 명확한 오류를 발생시킨다. 해당 블록·소스 줄을 진단에 표시한다.

### E4.5 제한 예시: 거부해야 할 코드

```python
import os                          # 불허: import

@on_hotkey("F6")
async def bad():
    open("secret.txt", "r")         # 불허: 파일 접근
    eval("1 + 2")                 # 불허: 동적 평가
    while True:
        pass                      # 불허: 취소 불가능한 바쁜 루프
```

표준 Python이 이를 실행할 수 있다는 사실은 Macro Studio DSL이 이를 허용한다는 의미가 아니다. 진단은 `UNSUPPORTED_SYNTAX`, `UNKNOWN_API`, `UNBOUNDED_LOOP` 등 구조화된 코드로 반환한다.

---

## E5. 공통 Python API 및 서비스 등록

### E5.1 API 표준 표면(초기 계약)

> 실제 저장소에서 기존 opcode나 API 인자명이 다르면 적응층을 둔다. Python API는 기존 IR로 변환되는 선언적 인터페이스이지 직접 실행 함수가 아니다.

| 네임스페이스 | 호출(예시) | 필요한 서비스/IR |
|---|---|---|
| `macro` | `await macro.sleep(ms=100)` | 비차단 wait |
| `macro` | `await macro.stop()` | Task 종료/취소 |
| `keyboard` | `await keyboard.tap("A", hold_ms=30)` | key_tap |
| `keyboard` | `await keyboard.down("LEFT")`, `await keyboard.up("LEFT")` | 키 소유권 제어 |
| `keyboard` | `await keyboard.repeat("A", frequency_hz=200, hold_ms=2, count=100)` | 전용 tap_sequence |
| `mouse` | `await mouse.click(button="left")` | mouse click |
| `mouse` | `await mouse.move_to(x=100, y=200, profile_id="builtin.natural", duration_ms=300)` | v1.2 Mouse Motion |
| `vision` | `await vision.find(template_id="template.confirm", timeout_ms=1000)` | v1.3 vision_find |
| `vision` | `await vision.wait_for(template_id="template.confirm", condition="appear", timeout_ms=5000)` | v1.3 vision_wait |
| `workflow` | `await workflow.call("click_confirm")` | workflow call |
| `actions` | `await actions.run("action.primary")` | 등록된 Action Registry (있을 때) |
| `navigation` | `await navigation.move_to(target=..., policy_id="...")` | R-2 Navigation Service (설치 시) |
| `spatial` | `await spatial.observe(source_id="...")` | R-1 Spatial Service (설치 시) |

- `keyboard.repeat()`는 **반복마다 Python 함수를 호출하는 방식이 아니라**, 기존 고주파 입력 전용 IR 한 개로 낮춘다.
- `mouse.move_to`는 Motion Generator 및 Output Router를 그대로 사용한다. Python에서 마우스 지터 경로를 새로 계산하지 않는다.
- `vision.find`는 v1.3 Perception IPC를 사용하고 타임스탬프·결과 유효기간을 유지한다.
- 이미 등록된 서비스가 없으면 해당 이름을 묵시적으로 생성하지 않는다. `MISSING_CAPABILITY` 또는 `UNAVAILABLE_SERVICE` 진단을 반환한다.
- 키/마우스 호출은 어떤 코드 작성 모드에서든 ARM/포커스/키 해제 정책을 우회할 수 없다.

### E5.2 Service Manifest 계약

등록 서비스는 Python 소스 코드 파일을 직접 제공하는 대신 **선언적 API 설명자**를 먼저 노출한다.

```json
{
  "service_id": "navigation.move_to",
  "api_version": "1",
  "python_symbol": "navigation.move_to",
  "kind": "async_action",
  "parameters": [
    {"name": "target", "type": "Position", "required": true},
    {"name": "policy_id", "type": "String", "required": true}
  ],
  "returns": "NavigationResult",
  "ir_opcode": "navigation_move_to",
  "required_capabilities": ["spatial.observe", "output.keyboard"],
  "interruptible": true
}
```

- Registry는 버전별 스키마 및 allowlist로 관리한다. `python_symbol`의 모든 속성 접근을 자동 허용하지 않는다.
- 서비스/오퍼레이션 ID와 인자·결과 타입은 원천을 검증하며, 프로젝트 저장 시 필수 서비스 식별자와 버전 범위를 기록한다.
- **코어에 게임 특화 API를 박지 않는다.** 특정 게임/서비스의 명령은 Application Pack의 등록 서비스/Action Registry를 통해 노출한다.
- Manifest 파일 자체는 실행 코드를 포함하지 않는다. 별도 코드 플러그인 로딩은 v1.4의 범위 밖이다.
- 어떤 서비스가 Perception Worker/Runner/Output Router 중 어디에서 구현되는지는 기존 서비스가 결정한다. Python 컴파일러가 직접 장치 드라이버와 통신하지 않는다.

### E5.3 Capability 검사

`CapabilityCheck`는 적어도 다음을 검사한다.

- 필수 서비스/버전이 설치되어 있는가?
- 매크로 프로젝트에서 참조한 이미지 템플릿/프로파일/액션 ID가 유효한가?
- 실행 백엔드가 요구 키·마우스 명령을 지원하는가?
- External HID에서 절대 좌표 이동 등 지원되지 않는 기능을 조용히 대체하고 있지는 않은가?
- 선택된 대상 창 및 실행 정책이 필요한가?

세션별 출력 채널 선택은 기존 v1.1 설계를 유지한다. Python 소스의 API 호출명에 `SendInput`/`HID`를 하드코딩하지 않는다.

---

## E6. Python AST 컴파일러 설계

### E6.1 컴파일 파이프라인

```text
source.py
  ↓ ast.parse (구문 검사)
Python AST
  ↓ allowlist 검사 / 심볼 해석
Typed DSL AST
  ↓ API Registry / 타입 / 반복·시간·Capability 검사
Validated ProgramModel
  ↓ existing IR lowering / block-id/source mapping
Versioned Common IR
  ↓ existing Validator
Immutable Execution Snapshot
```

**절대로 `exec`/`eval`로 Python을 실행하고 런타임 객체를 관찰해서 IR을 얻지 않는다.** AST 방문자가 허용된 노드만 의미 구조로 변환해야 한다.

### E6.2 노드 검사 정책

- AST 노드를 **명시적 allowlist**로 검사한다. 목록 외 문법은 거부한다.
- 등록된 호출의 네임스페이스/메서드명을 **정적 문자열**로 확인한다. 동적 함수 참조·별칭·임의 속성 접근은 거부한다.
- API 인자의 자료형과 개수, 기본값, 범위, 키워드 이름을 검사한다.
- 모든 이름은 선언된 로컬/전역/워크플로/API Registry에서 해석한다.
- 재귀·호출 깊이·핸들러 수·변수 수·노드 수·문자열 길이·반복 예산 상한을 설정한다.
- 부작용 있는 생성자, 임의 Python 객체 및 dunder에 접근할 수 없다.
- Python 포맷/주석은 컴파일에 필수 의미가 아니라 편집 메타데이터다.

### E6.3 IR 생성 예시

입력:

```python
@on_hotkey("F6")
async def tap_one_hundred():
    await keyboard.repeat("A", frequency_hz=200, hold_ms=2, count=100)
```

의미상 생성되는 IR(기존 IR 이름에 맞춰 매핑):

```json
{
  "handlers": [
    {
      "id": "handler.tap_one_hundred",
      "event": {"type": "hotkey", "key": "F6"},
      "workflow_id": "workflow.tap_one_hundred",
      "reentrancy_policy": "ignore_if_running"
    }
  ],
  "workflows": {
    "workflow.tap_one_hundred": {
      "steps": [
        {
          "op": "tap_sequence",
          "key": "A",
          "frequency_hz": 200,
          "hold_ns": 2000000,
          "count": 100,
          "missed_tick_policy": "skip_missed"
        }
      ]
    }
  }
}
```

이 예시는 간략화된 **의미 IR**이다. 실제 IR 필수 필드(버전, project_id, block_id, session settings 등)를 생략하지 말고 기존 스키마에 맞춰 생성한다. 키 표현도 기존 key/scancode 변환 계약을 재사용한다.

### E6.4 실패 및 오류 정책

컴파일 실패 시:

1. 오류 목록을 반환하며 **IR 실행/ARM이 불가능**한 상태로 표시한다.
2. 오류는 Python 줄/열, 가능한 경우 관련 블록 ID, 오류 코드, 원인, 수정 방향을 포함한다.
3. 지원하지 않는 문법을 우회하기 위해 Python 코드를 실행하거나 무시하지 않는다.
4. 컴파일 과정은 사용자 프로젝트 파일/원본 텍스트를 임의로 고치지 않는다.
5. 의존 서비스가 없으면 서비스 관련 오류로 구분한다. 일반 문법 오류로 뭉개지 않는다.

예시:

```json
{
  "code": "UNKNOWN_API",
  "severity": "error",
  "message": "지원하지 않는 호출입니다: os.system",
  "source": {"file": "macro.py", "line": 7, "column": 5},
  "block_id": null,
  "suggestion": "등록된 Macro Studio API만 사용하세요."
}
```

### E6.5 실행 의미론 일치

- 같은 IR은 블록 생성이든 Python 생성이든 같은 이벤트 순서·재진입·키 소유권·취소 규칙을 가진다.
- `await macro.sleep()`는 Task 대기로 변환하며 UI 스레드나 입력 스케줄러를 차단하지 않는다.
- 비차단 인식, 위치 관측, 내비게이션은 서비스의 완료/타임아웃 상태에 따라 Task를 재개한다.
- `STOP`/포커스 이탈/오류 시 사용 중인 출력 이벤트가 정리된다.
- 반복문 내부의 API 호출은 IR을 통해 실행되고, 기존 Input Dispatcher만 물리 입력을 생성한다.
- LLM 코드도 사람이 작성한 코드와 동일한 한계·검증·작업 취소를 적용한다.

---

## E7. 블록 ↔ Python 양방향 변환 규약

### E7.1 실현 가능한 약속

**지원되는 공통 의미 부분집합에서의 왕복 변환**만 보장한다. 다음은 보장하지 않는다.

- Python 원문의 주석, 공백, 빈 줄, 괄호 표기를 완벽히 보존
- Blockly의 블록 좌표/색상/묶음을 Python에서 표현
- 임의 Python 라이브러리·컴프리헨션·클래스를 블록으로 자동 변환
- 임의 커스텀 블록을 뜻이 맞는 Python으로 자동 추론

### E7.2 변환 경로

```text
[Blockly Workspace] ─compile─> [Typed ProgramModel / Common IR]
                                                │
                                                ├─generate─> Canonical Python
                                                │
[Supported Python] ─parse + validate─────────────┘
                                                │
                                                └─render─> Blockly Workspace
```

- 블록 → Python: Blockly Workspace를 **기존 컴파일러로 IR**로 만든 뒤, IR에서 정형 Python을 생성한다. Blockly JS 코드 생성기를 단독 실행 의미의 진실 공급원으로 두지 않는다.
- Python → 블록: Python AST를 검증해 IR로 변환한 뒤, 그 IR을 Blockly로 렌더링한다.
- 차이가 있는 블록·API는 **대응표(coverage matrix)**로 관리한다.
- v1.3 이미지 인식 블록과 v1.2 모션 블록은 가능한 한 v1.4의 Python 표준 API로 변환한다.
- 지원되지 않는 블록은 Python의 설명 주석으로 대체하고 실행 의미를 버리는 식으로 변환하면 안 된다. 변환은 명시적으로 실패해야 한다.

### E7.3 동일성 비교

`compile_python(generate_python(IR))` 및 `compile_blocks(render_blocks(IR))`의 결과는 사소한 ID, 소스 위치, UI 배치, 숫자 포맷을 정규화한 뒤 **동일한 실행 의미**를 가져야 한다.

정규화 항목은 `docs/PYTHON_ROUNDTRIP_CONTRACT.md`에 명시한다. 특히 다음은 비교 대상에서 제외하면 안 된다.

- 핸들러 이벤트/핫키, 재진입 정책, 우선순위
- 실행 워크플로 호출 순서 및 변수 의미
- 반복 횟수, 대기 시간 및 주파수/홀드 지속 시간
- 인식 템플릿 ID, timeout, 상태 판정, ROI/프로파일 ID
- Mouse Motion Profile ID, 출력 채널 Capability 요구사항
- 작업 취소·실패·분기 처리 의미론

### E7.4 stable ID 및 진단 매핑

- Python AST의 `lineno`, `col_offset`, `end_lineno`, `end_col_offset`을 SourceSpan으로 보관한다.
- 블록 컴파일 시 기존 `block_id`를 보관한다.
- 양방향 변환 시 가능한 범위에서 기존 Symbol/Workflow/Handler ID를 보존한다.
- Python 신규 코드에서 생성한 ID는 해당 소스 범위/프로그램 구조로 재현 가능한 stable mapping을 사용한다. 단순 줄 번호만으로 ID를 정하지 않는다.
- 실시간 실행 로그에서 Python 줄 또는 블록을 클릭하면 대응 위치가 강조 표시되도록 한다.
- Python 코드에 `# block_id=...` 같은 주석을 필수로 삽입하지 않는다. 연결정보는 별도 SourceMap으로 관리한다.

---

## E8. Python 편집기 UX / 메뉴 및 상태

### E8.1 메인 UI

기존 Blockly 작업 영역 위(또는 문서 탭)에 다음 편집 모드 전환을 추가한다.

```text
[파일] [편집] [실행] [마우스] [이미지 인식] [설정]

 프로젝트: Example              [블록] [Python]
 ┌─────────────────────────────────────────────────────┐
 │ Python Editor                                       │
 │ @on_hotkey("F6")                                    │
 │ async def ...                                       │
 │     ...                                             │
 └─────────────────────────────────────────────────────┘
 오류 0 · 경고 1 · 현재 편집 상태: Valid
 [검증] [블록으로 전환] [Dry Run] [ARM] [실행] [중지]
```

- 편집기는 고정폭 폰트, 줄 번호, Python 구문 강조, 자동 들여쓰기, Ctrl+Z/Y, 검색, 오류 밑줄, 소스 위치 이동을 제공한다.
- PySide6/QWebEngine 구성을 재사용하되, Monaco/CodeMirror/Qt 위젯 중 구현·배포 가능한 편집기를 선택한다. **웹 에디터 자체가 Python 코드를 실행하지 않도록 한다.**
- Python 편집에서 문법 검증·IR 컴파일은 별도 작업으로 실행하여 GUI에 큰 지연을 주지 않는다.
- 편집기 상단에 모드, DSL 버전, 사용 서비스 목록과 Dry Run/ARM 상태를 표시한다.
- Python 편집 중에도 프로그램 실행 채널(SendInput/External HID)은 기존 실행 설정에서 선택한다.

### E8.2 검증 및 코드 미리보기

- `검증`: 즉시 Python DSL 컴파일, 오류·경고·사용 Capability 보여주기
- `생성된 Python 보기`: 블록에서 생성한 코드를 읽기 전용으로 열 수 있음
- `블록으로 전환`: 정적 변환 가능성 검사 후 전환
- `프로젝트 저장`: 유효하지 않은 초안도 저장 가능; 실행은 금지
- `Dry Run`: 실제 입력 없이 Action/Perception 요청 및 예상 이벤트 타임라인을 관찰
- `코드 가져오기`: `.py` 파일을 텍스트로 로드, 검증 후 프로젝트에 반영 (임의 Python 파일의 **실행** 아님)
- `코드 내보내기`: 정형 DSL `.py`로 저장; 일반 Python으로 독립 실행된다는 문구 금지

### E8.3 실수 방지와 실패 안내

- 잘못된 Python을 가진 상태에서 `블록으로 전환` 시 오류 위치와 미지원 기능을 표시하고 원본 코드를 보존한다.
- 지원 블록 중 변환하지 못한 opcode가 있으면 사용자에게 해당 블록·사유를 표시한다.
- 실행 도중 코드 변경 시 현재 세션은 기존 불변 IR Snapshot 사용. 변경된 코드는 다음 LOAD/START에서 검증 후 적용한다.
- `ARM`은 사용자가 명시적으로 수행한다. LLM 코드 붙여넣기 또는 Compile 성공만으로 ARM하지 않는다.
- STOP은 편집 모드와 관계없이 항상 동일한 긴급중지 경로를 사용한다.

---

## E9. LLM 코드 생성 지원 (외부 LLM 우선)

### E9.1 제품 전략

v1.4에서는 **앱 내부에서 모델 API를 직접 호출하지 않아도** LLM이 안정적으로 코드를 생성하도록 한다.

1. 버전 지정된 `MACRO_STUDIO_PYTHON_API.md`와 `DSL_GRAMMAR.md`를 제공한다.
2. 최소 8개 이상의 대표 예제(핫키, 반복, 자율 루프, 이미지 탐색, 마우스 프로파일, 액션 호출, 오류 처리, 복합 이벤트)를 제공한다.
3. 외부 모델용 `LLM_AUTHORING_PROMPT.md`와 JSON 형태의 API Capability 목록을 내보낼 수 있게 한다.
4. 사용자는 코드 붙여넣기 → 검증 → 실패 진단 복사 → 수정 → 블록 확인 → Dry Run 순서로 사용한다.
5. 일반 Python 코드가 반환되더라도 불허 문법이 있으면 정확히 실패시키고 수정 예를 제공한다.

### E9.2 프롬프트 컨텍스트 최소화

모델에 제공할 수 있는 정보는 다음과 같이 제한한다.

- DSL 버전 / 사용 가능한 API 함수 및 정확한 인자
- 현재 프로젝트에 등록된 템플릿·모션 프로파일·서비스/액션의 **ID 및 공개 별칭**
- 작업 목적과 출력 방식, 필요시 예제
- 런타임 한도 및 안전 규칙

- 이미지 원본, 캡처 프레임, 경로, 개인 식별자, API 키, 프로그램 로그 전체를 기본적으로 외부에 보내지 않는다.
- 미설치 서비스/없는 템플릿을 모델이 임의로 발명하지 않도록 목록을 제공한다.
- API 설명은 신뢰 가능한 로컬 Registry에서 생성한다. 프로젝트/모델 텍스트는 지시가 아닌 데이터로 취급한다.

### E9.3 결과 검증 UX

- LLM 생성 Python은 **외부에서 제공된 비신뢰 텍스트**로 취급한다.
- 검증 항목: DSL 문법, 서비스 존재, 키/ROI/시간/횟수 범위, 종료 가능성, 사용 템플릿/프로파일 유효성, 출력 Capability.
- `Validate` 결과 화면에서 권한/출력 요구사항과 정적 오류를 요약한다.
- 수정용으로 복사 가능한 진단 형식(오류 코드, 줄, 설명, 지원 대안)을 제공한다.
- 생성 결과를 기존 프로젝트에 덮어쓰기 전에 변경 Diff를 표시하고 승인받는다.
- **LLM이 제안한 코드는 절대 자동 실행하지 않는다.**

### E9.4 내장 AI 및 IR 직접 생성

- 앱 내부 LLM 채팅, 공급자 설정·토큰 저장, 자동 코드 수정은 **v1.5+**로 미룬다.
- LLM → IR 직접 생성은 후속 확장 접점을 남기되, v1.4 필수 기능으로 두지 않는다. 후속에 구현해도 동일 Validator 및 ARM 정책을 적용한다.

---

## E10. 실행 안전성·정확성·성능

### E10.1 안전 모델

**Standard Python DSL은 임의 Python을 실행하지 않는다는 점에서 공격 표면을 줄이지만, 이를 일반적인 의미의 완전한 샌드박스라고 부르지 않는다.** 서비스 자체의 버그·운영체제 권한·외부 장치 동작은 별도 방어가 필요하다.

- AST allowlist + 정적 이름/타입 검증을 반드시 적용.
- Runner는 검증된 IR만 받으며, 전달받은 원본 Python 파일을 import하지 않는다.
- 프로그램이 자기 자신/외부 프로그램을 종료하거나 파일에 접근하는 Python 표준 API는 제공하지 않는다.
- 잘못된 반복·이벤트 폭주·출력 예약 폭주 방지: 최대 작업 수, 반복 횟수, 큐 길이, 시간·명령 예산 한도.
- STOP, UI 종료, 대상 창 포커스 상실, 세션 취소의 키 해제 규칙은 v1.0~v1.3을 유지.
- 하드웨어 External HID의 Watchdog/Heartbeat/RELEASE_ALL 정책은 v1.1을 유지.
- 시각 인식 결과의 프레임 나이/UNKNOWN/좌표 공간 유효성은 v1.3 및 R-1 계약 유지.
- 새로운 실행 경로는 `Output Router`를 우회하지 않는다.

### E10.2 성능 조건

- Python → IR 컴파일 시간은 UI 응답성을 해치지 않도록 비동기 처리하거나 표시 가능한 응답 경계를 확보한다. 수치는 실제 프로젝트 규모별로 측정해 기록한다.
- 컴파일과 코드 생성은 **런타임 고주파 입력 경로에서 일어나지 않는다**.
- `keyboard.repeat`는 이미 예약된 IR 고속 명령으로 실행한다. Python 소스의 `for`문을 실시간으로 해석하면서 주기를 만드는 방식을 사용하지 않는다.
- 서비스 대기(`vision.find`, `macro.sleep`, `navigation.move_to`)는 기존 Task 스케줄러에서 비차단으로 처리한다.
- UI 미리보기/로그 전송의 갱신 주기가 입력 타이밍을 결정하거나 방해하지 않도록 한다.
- 검증 성능의 측정값을 기획 목표와 구분하여 문서화한다. Windows 일반 사용자 모드에서 하드 실시간 타이밍을 보장한다고 주장하지 않는다.

### E10.3 Replay/Dry Run

- 기존 FakeBackend/DryRun을 재사용하고, Python/블록에서 컴파일된 IR의 이벤트 로그를 동일 형식으로 비교한다.
- Recognition/Spatial 서비스는 녹화 프레임 또는 Fake 결과를 주입할 수 있어야 한다.
- 드라이런 중 실제 마우스/키보드 이벤트가 발생하지 않아야 한다.
- 일부 외부 서비스가 설치되지 않았을 때는 모의 입력만으로의 테스트 가능 여부와 실제 실행 불가 이유를 구분한다.

---

## E11. 제안 코드 구성

> 아래는 책임 경계의 예시다. 기존 저장소 파일명에 맞춰 배치하되 `python_dsl`이 특정 게임/서비스 구현에 직접 의존하지 않아야 한다.

```text
src/macro_studio/
├── compiler.py                # 기존 Blockly → IR 유지
├── runtime.py                 # 기존 Task/Runner 유지
├── python_dsl/
│   ├── ast_parser.py          # Python 소스 → AST
│   ├── allowed_nodes.py       # 지원 문법 allowlist
│   ├── symbols.py             # 심볼/스코프
│   ├── types.py               # DSL 타입
│   ├── api_registry.py        # 등록 API/서비스 계약
│   ├── semantic.py            # 정적 검사
│   ├── lowering.py            # Typed AST → 공통 IR
│   ├── codegen.py             # 공통 IR → canonical Python
│   ├── blocks_renderer.py     # 지원 IR → Blockly 표현
│   ├── source_map.py          # Block ID ↔ Source Span
│   └── diagnostics.py         # 오류 코드/위치/가이드
├── editor/
│   ├── python_editor.*        # UI 기술은 저장소에 맞춤
│   ├── mode_switch.*
│   └── validation_view.*
├── services/
│   └── manifests/             # 등록된 범용 서비스의 정적 API 계약
└── ...                        # 기존 vision/motion/output 등

tests/
├── python_dsl/
│   ├── test_syntax.py
│   ├── test_types.py
│   ├── test_lowering.py
│   ├── test_codegen.py
│   ├── test_roundtrip.py
│   ├── test_registry.py
│   ├── test_diagnostics.py
│   ├── test_security.py
│   └── test_migrations.py
└── integration/
    ├── test_python_vision_mouse_dryrun.py
    ├── test_block_python_semantics.py
    └── test_python_cancel_output.py

docs/
├── PYTHON_V1_4_GAP.md
├── PYTHON_API.md
├── PYTHON_DSL_GRAMMAR.md
├── PYTHON_ROUNDTRIP_CONTRACT.md
├── PYTHON_LLM_AUTHORING_PROMPT.md
├── VERSIONING.md
└── DECISIONS.md

examples/
├── python/hotkey_action.py
├── python/autonomous_loop.py
├── python/image_find_move_click.py
├── python/repeating_tap.py
└── python/shared_workflow.py
```

---

## E12. 단계별 구현 계획 (단계별 Codex 작업)

### PY-0 — 저장소 분석, Gap 문서, 공통 계약 확정

1. 실제 repo에서 Blockly serializer, IR Validator, Runner, 프로젝트 스키마/마이그레이션, Perception IPC, Motion Profile, Output Router, 서비스 레지스트리의 존재 여부를 확인한다.
2. v1.4 표준 DSL의 API 명과 기존 opcode 대응표를 작성한다.
3. 지원 가능 블록/IR 변환 매트릭스 및 누락 서비스 목록을 작성한다.
4. 기존 v1.3 프로젝트를 깨지 않는 저장·마이그레이션 방식을 결정한다.
5. `docs/PYTHON_V1_4_GAP.md`, `docs/PYTHON_ROUNDTRIP_CONTRACT.md`, `docs/DECISIONS.md`를 작성/갱신한다.

**완료:** 저장소의 사실과 목표 명세가 구분되어 문서화됨. 이 단계에서는 UI·Runner를 전면 재작성하지 않음.

### PY-1 — DSL 문법·API Registry·정적 분석

1. Python `ast.parse` 기반 파서, AST allowlist, 진단, Symbol Table/기본 타입을 구현한다.
2. `@on_start`, `@on_hotkey`, `@on_interval`, `@workflow` 선언을 지원한다.
3. `await macro.sleep`, 기본 키보드·마우스 명령의 정적 호출을 지원한다.
4. 거부 문법(import, exec, eval, getattr, lambda, class, unbounded busy loop) 단위 테스트를 구축한다.
5. 허용 API Manifest, 호출 인자 검증, 진단 source span을 구현한다.

**완료:** 지원 문법과 금지 문법에 대한 정확한 통과/실패, `exec` 경로 없음.

### PY-2 — Python → Common IR 컴파일 및 Dry Run

1. 파싱된 Typed AST를 v1.3 IR의 Handler/Workflow/Step 구조로 변환한다.
2. 변수, if/else, 반복, 워크플로 호출, 서비스 결과 필드 접근을 확장한다.
3. 기존 Validator와 Runtime의 한도/취소/재진입 정책을 재사용한다.
4. FakeBackend에서 `keyboard.repeat`, 마우스 이동, 이벤트 시작/정지를 확인한다.
5. Python 소스→IR Snapshot fingerprint를 계산해 오래된 IR의 자동 실행을 방지한다.

**완료:** Python DSL 예제가 **직접 Python 실행 없이** 기존 IR Runner에서 Dry Run으로 성공.

### PY-3 — IR → Canonical Python 및 Blockly 왕복

1. 기존 Blockly 블록의 IR 변환 Coverage를 조사하고, v1.0~v1.3 핵심 블록의 codegen을 구현한다.
2. Python → IR → Blockly Renderer를 구현한다.
3. 왕복 의미 동등성 테스트를 작성한다.
4. 변환 불가능한 opcode는 명확한 진단을 내고 변환을 거절한다.
5. SourceMap과 Handler/Workflow 안정 식별자 복구를 구현한다.

**완료:** 핫키, 조건, 반복, 대기, 인식, Mouse Motion 프로파일의 양방향 의미 동등성 테스트 통과.

### PY-4 — PySide6 Python Editor 및 UX 통합

1. 기존 메인 화면에 `[블록] [Python]` 편집 모드 선택 기능을 추가한다.
2. Python 편집기의 구문 강조·줄 번호·오류 위치·수정 상태를 구현한다.
3. 검증·변환·저장·로드·소스 가져오기/내보내기 UI 연결.
4. Draft 보존, 전환 실패 시 원본 보존, 실행 중 소스 변경 정책 구현.
5. 기존 ARM/Dry Run/STOP/Output Channel 선택 UI를 유지한다.

**완료:** 사용자가 GUI에서 코드 수정→검증→블록 변환→Dry Run까지 수행. 컴파일 오류 때 실행 불가.

### PY-5 — LLM Authoring Pack + 서비스 확장

1. API 문서, 문법 제한 표, 샘플 8종 이상, 외부 LLM 프롬프트 템플릿 생성.
2. 현재 활성 서비스와 공개 심볼·템플릿·모션 프로파일 목록을 내보내는 기능 개발.
3. 복사 가능한 구조화 진단 및 재생성용 피드백 형식 제공.
4. Application Pack별 서비스 Manifest를 등록하고 일반 Python DSL에서 호출 가능하도록 한다.
5. 최소 1개 **Auto Maple과 무관한** Application Pack/서비스와 1개 선택적 서비스의 컴파일/실행 테스트.

**완료:** 외부 LLM이 문서만으로 유효한 Macro Studio DSL 코드를 생성할 수 있고, 미지원 호출이 명확히 거부됨.

### PY-6 — 통합 테스트, 회귀·안전·문서화

1. v1.3 블록 프로젝트 열기·저장·실행 회귀 테스트.
2. SendInput/External HID Capability 차이 검사, 인식 Worker·Mouse Motion 연동 테스트.
3. STOP/포커스 이탈/오류/무한 루프/이벤트 폭주 경계 테스트.
4. GUI 테스트, 샘플 프로젝트, 문서, 사용자 가이드, 오류 코드 목록 작성.
5. Windows 실기기 검증은 사용자가 승인한 테스트 창/환경에서만 진행하고 결과 기록.

**완료:** E13의 필수 인수 기준 모두 통과, 알려진 한계와 버전 정리가 문서화됨.

---

## E13. 테스트 매트릭스 및 인수 기준

### E13.1 자동 테스트

| ID | 입력 / 상황 | 기대 결과 |
|---|---|---|
| PY-T01 | 기본 핫키 핸들러 Python | Handler IR 생성, 유효성 통과 |
| PY-T02 | 이벤트 핸들러 중복 정의 | 중복 ID 진단 |
| PY-T03 | `await macro.sleep` | 비차단 wait IR, Task 대기/취소 지원 |
| PY-T04 | `keyboard.tap/repeat` | 기존 key_tap/tap_sequence 의미 동등 |
| PY-T05 | 조건문/변수/for 반복 | Python↔IR 실행 순서 보존 |
| PY-T06 | 양보 없는 `while True` | 안전한 컴파일 실패 |
| PY-T07 | 허용된 반복+취소 | STOP 수신 후 종료 |
| PY-T08 | `import os` | `UNSUPPORTED_SYNTAX` 또는 정책 위반 오류 |
| PY-T09 | `exec/eval/open` | 컴파일 거부; 실행 흔적 없음 |
| PY-T10 | dunder/getattr 동적 접근 | 정적 검증 거부 |
| PY-T11 | 알 수 없는 API | `UNKNOWN_API` + 위치 |
| PY-T12 | 잘못된 인자 타입/키워드 | `TYPE_ERROR` / `INVALID_ARGUMENT` |
| PY-T13 | 없는 템플릿/프로파일/서비스 | `UNKNOWN_ASSET` / `UNAVAILABLE_SERVICE` |
| PY-T14 | Vision `FOUND` 결과 | 좌표 추출→Motion 명령으로 전달 |
| PY-T15 | Vision `UNKNOWN` / timeout | 오인식 없이 명시적인 오류/분기 처리 |
| PY-T16 | stale frame / calibration 변경 | 좌표 이용 거부 및 안전 중단 |
| PY-T17 | 블록→Python→IR | 정규화 의미 동등 |
| PY-T18 | Python→IR→블록→IR | 정규화 의미 동등 |
| PY-T19 | 비지원 블록/문법 | 변환 거절, 원본 보존 |
| PY-T20 | Python 구문 오류 상태 저장 | Draft 저장 가능, ARM/START 불가능 |
| PY-T21 | 편집 후 이전 IR snapshot 존재 | stale IR 자동 실행 거부 |
| PY-T22 | 구버전 v1.3 프로젝트 로드 | 블록/프로파일/이벤트 보존 |
| PY-T23 | 정형 Python 생성 결정성 | 같은 IR → 같은 Python 텍스트 |
| PY-T24 | Python SourceMap | 오류·실행 위치 정확히 매핑 |
| PY-T25 | External HID 미지원 명령 | 실행 전 Capability 오류 |
| PY-T26 | FakeBackend Dry Run | 실제 입력 호출 0건 |
| PY-T27 | ARM 미수행 실입력 시도 | Output Router에서 차단 |
| PY-T28 | STOP/포커스 이탈 | 예정 입력 취소·키 해제 |
| PY-T29 | 대규모 AST/깊은 호출/이벤트 폭주 | 정책 상한으로 안전 실패 |
| PY-T30 | 서비스 없는 환경 | Core/Editor 정상 실행, 미지원 서비스만 경고/거부 |
| PY-T31 | 특화 Application Pack import 없음 | DSL/Core 테스트 독립 통과 |
| PY-T32 | 외부 LLM이 존재하지 않는 호출 생성 | 수정 가능한 정확한 진단, 자동 실행 없음 |
| PY-T33 | Vision/Mouse/Output 블록과 Python 동등 워크플로 | Fake 출력 이벤트 및 결과 상태 동일 |
| PY-T34 | 컴파일 중 GUI 편집/중지 | UI 멈춤·입력 생성 없음 |
| PY-T35 | 프로젝트에 텍스트 초안·블록 캐시 공존 | 활성 소스, fingerprint 및 실행 스냅샷 규칙 준수 |

### E13.2 수동 GUI/Windows 확인

- PY-M01: 블록 탭에서 Python 전환 시 코드를 확인할 수 있다.
- PY-M02: Python 코드 수정 후 블록 전환 시 의미가 유지된다.
- PY-M03: 유효하지 않은 Python은 오류 줄이 강조되고 ARM이 비활성화된다.
- PY-M04: LLM 코드 붙여넣기 후 사용 서비스/권한과 Dry Run 로그를 확인할 수 있다.
- PY-M05: 실행 채널 변경 시 Python 코드는 바뀌지 않으며 지원 Capability를 점검한다.
- PY-M06: 창 포커스 이탈/긴급 중지 시 기존 안전 동작이 그대로 적용된다.
- PY-M07: 화면 인식 + 마우스 모션 결합 샘플이 승인된 테스트 창에서 동작한다.

### E13.3 대표 End-to-End 시나리오

**목표:** 외부 LLM이 작성한 Python 코드가 블록 코드와 동일한 환경에서 화면 요소를 찾아 지정 모션 프로파일로 이동한 뒤 클릭한다.

1. 샘플 테스트 창(또는 녹화 프레임)과 `template.confirm`을 준비한다.
2. Python Editor에 E4.3의 `click_confirm` 코드를 붙여넣는다.
3. 검증에서 요구 서비스·자산·출력 Capability가 표시되고 오류가 없음을 확인한다.
4. Python → Blockly로 변환한 뒤 블록/IR 동일성 테스트를 한다.
5. Dry Run에서 `vision.find → mouse.move_to → mouse.click` 순서를 확인한다.
6. 템플릿 미검출/인식 만료/창 포커스 이탈 시 예상치 못한 입력이 없는지 확인한다.
7. 선택된 출력 채널에서 미지원 기능이 있으면 오류를 표시하고 실행하지 않는다.
8. 실제 입력 확인은 별도 승인된 테스트 창에서만 수행한다.

### E13.4 Definition of Done

- [ ] 기존 v1.3 프로젝트와 핵심 기능 회귀 테스트 통과.
- [ ] 제한 Python DSL AST parser/validator/compiler가 있으며 사용자 Python 직접 실행 경로가 없음.
- [ ] Python DSL의 이벤트·반복·조건·워크플로·키보드/마우스·기본 인식 명령을 공통 IR로 생성.
- [ ] Blockly ↔ Python의 지원 부분집합에서 왕복 의미 동등성이 검증됨.
- [ ] 비지원 문법/블록/서비스/백엔드 기능이 **명시적 오류**로 보고됨.
- [ ] Python Editor, 검증·오류 표시·소스 보존·저장/로드, Dry Run이 GUI에서 연결됨.
- [ ] ARM/Focus/STOP/Input Arbiter/External HID 안전 규칙이 기존과 동일하게 적용됨.
- [ ] 외부 LLM용 API 문서·예제·프롬프트 및 구조화 진단을 제공.
- [ ] Auto Maple 없는 환경에서 기능 및 테스트가 독립적으로 동작함.
- [ ] 알려진 제한과 성능 검증 결과, 마이그레이션 정책이 문서화됨.

---

## E14. Codex 변경 관리 및 보고 양식

각 단계 종료 후 아래 형식으로 보고한다.

```text
1. 기준 저장소 상태
   - 확인한 커밋/브랜치:
   - 기존 구현과의 차이:

2. 이번 단계 범위
   - 수행한 PY 단계:
   - 변경한 파일:
   - 확정한 API / IR 계약:

3. 테스트
   - 실행 명령:
   - 통과/실패/미실행 건수:
   - 실입력 테스트 여부 (기본: 없음):

4. 안전·호환성
   - v1.3 회귀 영향:
   - ARM/포커스/STOP 검증:
   - 비지원 문법 및 Capability 처리:

5. 남은 작업
   - 미구현 요구사항:
   - 블로커/실측 필요 사항:
   - 다음 단계 제안:
```

문서와 테스트가 실제 구현보다 앞서 있으면 완료로 표시하지 않는다. 테스트를 건너뛴 경우 원인을 명시한다.

---

## E15. Codex에 바로 전달할 시작 프롬프트

아래 프롬프트를 그대로 사용한다.

```text
Macro Studio v1.4 Python Scripting & AI Code Generation의 개발을 시작해.

1. docs/SPEC.md(v1.3 통합 명세)와 docs/PYTHON_V1_4_SPEC.md(본 문서)를 읽고,
   저장소 실제 구현 상태를 확인해.
2. 본 문서 E0/PY-0 지침에 따라 docs/PYTHON_V1_4_GAP.md를 작성하고,
   R-0~R-6 범용 서비스의 실제 구현 여부를 확인해. 완료를 가정하지 마.
3. 이전 v1.4 룬 판독 파일은 Auto Maple 전용 확장으로 취급하고,
   이번 v1.4 목표는 Python DSL/블록 변환/LLM 생성 코드 검증으로 유지해.
4. 기존 v1.3 블록·IR·Runner·Perception·Motion·SendInput/External HID의
   안전/호환 규칙을 유지하고, 기존 코드를 전면 재작성하지 마.
5. PY-0~PY-6 중 최초 미완료 단계 하나만 구현하고, 테스트를 작성·실행해.
6. 실제 키보드/마우스 입력은 사용자가 명시적으로 ARM하지 않는 한
   절대 발생시키지 마. 자동화 테스트는 FakeBackend/Dry Run을 사용해.
7. 모든 Python DSL은 AST 검증 후 공통 IR로 컴파일해야 하며,
   사용자 Python을 exec/eval/import하여 직접 실행하는 경로를 만들지 마.
8. 결과 보고서에는 구현 내역, 코드 위치, 테스트 결과, 기존 기능의
   회귀 영향, 미구현 항목과 다음 단계를 포함해.
```

---

## E16. 우선 구현할 참조 예제 세트

> 예제는 **정규 DSL** 코드로 제공하며, 독립 CPython 실행을 보장하지 않는다. 테스트에는 의도적으로 미지원 API/에셋을 사용하지 않는다.

1. **Hotkey Action:** `F6` 입력 시 키 조합 실행.
2. **Autonomous Loop:** 시작 이벤트에서 조건에 따라 주기적으로 액션, 대기·취소 가능.
3. **High Frequency Tap:** 100/200Hz 전용 반복 입력 명령.
4. **Image Find & Click:** v1.3 템플릿 감지 후 좌표 이동·클릭.
5. **Mouse Motion:** v1.2 기본/블록별 프로파일 선택 및 궤적 이동.
6. **Shared Workflow:** 핫키/타이머에서 같은 재사용 워크플로 호출.
7. **Reactive + Autonomous:** 자율 루프 진행 중 핫키 작업; 기존 중단/재진입 정책.
8. **Service Capability:** 설치된 범용 서비스 호출, 미설치 시 진단.
9. **Error Example:** 존재하지 않는 템플릿·UNKNOWN 인식 결과 처리.
10. **Roundtrip Example:** 블록→Python→블록 의미 동등성 비교.

---

## E17. 최종 기술 결정 요약

| 질문 | v1.4 결정 |
|---|---|
| Python을 그대로 실행할 것인가? | **아니오.** 제한된 DSL을 AST로 검증하여 공통 IR로 컴파일 |
| 블록과 Python을 어떤 기준으로 통합할 것인가? | **실행 의미는 공통 IR 기준**, 편집 원본은 각각 보존 |
| Python 전체와 100% 양방향 변환? | **아니오.** 지원 문법 부분집합만 왕복 보장 |
| LLM API를 내장할 것인가? | **v1.4는 외부 LLM 생성 코드 지원**, 앱 내 LLM은 후속 |
| 게임별 명령은 어디에 둘 것인가? | Application Pack / 서비스 Registry, Core에는 넣지 않음 |
| 출력 채널 차이는 어디서 처리할 것인가? | 기존 Output Router 및 Capability 검증 |
| 안전성은? | AST allowlist + IR Validator + Dry Run + 명시적 ARM + 기존 STOP 정책 |
| R-0~R-6 완료가 선행조건인가? | **기본 서비스만으로 독립 착수 가능**, 미완료 서비스는 선택 기능·Capability로 처리 |
| 과거 룬 판독 `v1.4` 문서는? | 제품 버전과 구분되는 Auto Maple 전용 확장 |

**문서의 최종 지향점:** 사람은 블록이나 Python 중 편한 방법으로 작성하고, LLM은 정확한 Python API만 사용하여 코드를 생성한다. 그러나 어떤 출처로 작성됐든 프로그램은 동일한 IR Validator, Task Runtime, Perception/Navigation Services 및 Output Router를 통해 동일한 안전 규칙 아래 실행된다.
