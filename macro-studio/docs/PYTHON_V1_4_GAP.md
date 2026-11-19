# v1.4 Python Scripting 구현 차이

PY-0의 기준은 `main`의 `e4cfa9b`와 [Python Scripting 명세](../../MacroStudio_Python_Scripting_AI_Code_Generation_Spec_v1_4.md)다. 아래 기준 구현 대응표는 PY-0 조사 당시의 상태다. v1.4.0에는 PY-1~PY-6의 DSL, IR 컴파일, 왕복 변환, GUI·작성 자료와 인수 검증을 연결했다. [최종 검증과 한계](PYTHON_ACCEPTANCE.md)를 따른다.

## PY-4 편집기 연결

- 주 편집 영역의 `블록`과 `Python` 탭에서 활성 소스를 선택한다. 탭 전환은 별도 작업에서 컴파일·의미 동등성 검사를 마친 뒤 적용한다. 미지원 구성이나 오류를 만나면 원본 소스와 현재 모드를 보존한다.
- Python 편집기는 구문 강조, 줄 번호, 현재 위치, 사용 서비스 표시와 오류 줄 이동을 제공한다. `Python` 메뉴에서 읽기 전용 생성 코드 미리보기, `.py` 텍스트 가져오기·내보내기와 이전 편집본 복원을 사용할 수 있다.
- Python 자료가 생기면 프로젝트 스키마 2에 `editor.active_mode`와 `editor.python`을 저장한다. 구문 오류가 있는 초안도 저장할 수 있다. 기존 스키마 1은 블록 모드로 열고 그대로 저장한다.
- 블록 변환 전 워크스페이스는 `editor.previous_workspace`에 보존한다. Python을 재생성하거나 가져올 때 이전 소스는 `editor.python.previous_source`에 보존한다. 복원은 마지막 보존본과 현재 편집본을 교환한다. 생성 소스와 블록 ID 대응은 `editor.python.generated`에 저장하며, 소스가 그대로인 경우 재열기 후에도 검증된 소스맵에 적용한다.
- Python 수정은 검증 상태를 무효화한다. `검증` 성공 후 시작할 수 있으며, 시작 시 현재 소스와 프로젝트·프로파일을 다시 컴파일한다. 검증 중 편집·열기·저장 또는 설정 변경으로 입력이 달라지면 결과를 폐기한다. 파일에서 실행 스냅샷이나 ARM 권한을 복원하지 않는다.
- 실행은 기존 별도 Runner에 IR을 전달한다. 실행 중 Python을 수정해도 현재 세션은 시작한 IR을 사용한다. 다음 시작에는 새 소스 검증이 필요하다. ARM은 기존 명시적 사용자 동작과 로컬 설정을 사용한다.
- Python 단독 핫키처럼 Blockly의 제약과 다른 구문은 Python 검증을 통과해도 블록 전환에서 거부될 수 있다. 조합 핫키와 지원 부분집합은 왕복 계약을 따른다.

PY-5의 LLM Authoring Pack과 서비스 연결도 구현했다. PY-6의 최종 결과는 [인수 검증 보고서](PYTHON_ACCEPTANCE.md)에 기록한다.

2026-11-19 검증에서 전체 자동 테스트 339개가 통과했다. Python GUI 검사는 초안 저장·재열기, 스키마 1 호환, 실패한 변환의 원본 보존, 오래된 검증 결과 폐기, 읽기 전용 미리보기, 소스맵 재열기, 사용자 모션 프로파일 이식과 별도 Runner 드라이런·STOP을 확인했다. Windows 기본 렌더링의 실제 WebEngine 화면에서도 조합 핫키·반복·대기·프로파일 이동의 Python→Blockly→Python 의미 동등성과 오류 시 실행 차단을 확인했다. 기본 창과 최소 960×640 화면을 시각적으로 확인했다. 실입력 장치 재검증과 배포본 생성은 이번 단계의 검증에 포함하지 않았다.

## PY-5 작성 자료와 선택 서비스

`Python` 메뉴에 LLM 작성 자료 ZIP 내보내기, 서비스 Manifest 관리와 생성 코드 붙여넣기를 연결했다. ZIP에는 Registry에서 생성한 API 레퍼런스·Capability JSON, DSL 문법, 프롬프트, 공개 프로젝트 컨텍스트, 예제 8개와 데스크톱 예제 Manifest가 포함된다. [사용 절차](LLM_AUTHORING.md)와 [Manifest 계약](SERVICE_MANIFEST.md)을 따른다.

선택 서비스 `actions.run` v1은 프로젝트에 등록한 선언형 액션을 기존 IR로 변환한다. 현재 Manifest는 키 누르기·마우스 클릭·대기 명령을 지원한다. Spatial·Navigation 서비스 및 새 Runner opcode는 등록 범위 밖이며 명시적 진단으로 거부한다. 범용 서비스 R-0~R-6가 모두 구현되었다는 의미는 아니다.

프로젝트별 Registry·버전·액션 ID, Manifest allowlist, 선택 출력의 Capability, 스냅샷 변경 검사를 적용한다. 예제 8개와 별도 프레젠테이션 팩의 드라이런, 액션 STOP 시 키 해제, 오류 상태 분기, 미등록 서비스와 버전·출력 불일치, 개인정보 제외를 자동 검증했다. Windows 기본 렌더링의 실제 메뉴에서 코드 변경 승인, 진단 복사, 팩 등록, ZIP 저장과 별도 Runner 드라이런도 확인했다. 실입력 장치 재검증과 v1.4 배포본 생성은 PY-6에 남아 있다.

2026-11-19 최종 전체 회귀 검사에서 369개가 통과했다. API 레퍼런스와 Registry 일치 검사, 기본 창 및 최소 960×640 레이아웃 확인도 포함했다.

명세가 참조하는 `docs/SPEC.md`와 v1.3 통합 기획서는 로컬에서 확인되지 않았다. [v1.3 시각 인식 명세](../../MacroStudio_Visual_Recognition_Spec_v1_3.md), [v1.2 통합 명세](../../MacroStudio_Codex_Implementation_Spec_v1_2_Integrated.md)와 실제 코드를 기준으로 조사했다.

## 현재 구현

| 경계 | 기준 구현 | v1.4에서 필요한 작업 |
|---|---|---|
| Blockly 직렬화 | [editor.js](../src/macro_studio/editor/editor.js)가 `Blockly.serialization.workspaces.save/load`를 호출한다. [compiler.py](../src/macro_studio/compiler.py)가 프로젝트를 검증된 IR과 분리된 블록 목록으로 변환한다. | Python 프런트엔드를 추가한다. 두 방향 변환은 공통 IR을 거친다. |
| 프로젝트 | [gui.py](../src/macro_studio/gui.py)의 열기·저장은 최상위 `schema_version: 1`, `workspace`, 에셋 및 설정을 사용한다. 열기는 스키마 1만 허용한다. | Python 편집본을 위한 프로젝트 스키마 2와 구버전 읽기 경로가 필요하다. |
| 실행 IR | [ir.py](../src/macro_studio/ir.py)의 `validate_program`은 IR 1~3과 이벤트, 타입, 명령, 참조를 검증한다. | 기존 의미를 표현할 수 있는 호출에는 현재 opcode를 쓴다. 새 의미가 필요한 호출만 별도 IR 확장을 검토한다. |
| Runner와 출력 | [editor_runner.py](../src/macro_studio/editor_runner.py), [runtime.py](../src/macro_studio/runtime.py), [live.py](../src/macro_studio/live.py), [output.py](../src/macro_studio/output.py)가 프로세스 분리, 비차단 Task, 키 정리, ARM·포커스 및 출력 선택을 담당한다. | Python 소스와 소스맵은 편집 계층에서 다룬다. 기존 Runner에는 검증된 IR과 실행 설정을 보낸다. |
| 모션과 인식 | [모션 프로파일 Registry](../src/macro_studio/mouse_motion/registry.py), [PerceptionService](../src/macro_studio/perception.py), [인식 프로파일](../src/macro_studio/vision_profiles.py)이 있다. Perception IPC에는 워커 세션 ID, 요청 ID와 메시지 버전이 있다. | 기존 프로파일 해석과 타임스탬프·결과 만료 계약을 사용한다. |
| Python과 범용 서비스 | 기준 코드에 Python DSL 파서·API Registry·SourceMap·코드 작성 모드나 서비스 Manifest 등록기가 없다. 모션·인식 프로파일 저장소와 OutputRouter는 각 기능의 전용 계약이다. | 선언적 API allowlist와 진단을 추가한다. 서비스 선택 기능은 등록 및 검증 가능한 계약이 생긴 뒤 연결한다. |

## Python API와 기존 opcode 대응

| 명세의 API 또는 구문 | 대상 IR | 범위와 차이 |
|---|---|---|
| `@on_start`, `@on_hotkey`, `@on_interval` | `handlers[].event.type`의 `project_start`, `hotkey_pressed`, `interval` | 기존 핫키 정규화와 Windows 라이브 조합키 제약을 적용한다. 간격은 `period_ns`로 변환한다. |
| `@workflow`, `workflow.call` | `workflows`, `call`, `return` | 기존 타입 있는 인자·반환·결과 저장 규칙을 쓴다. |
| `macro.sleep`, `macro.stop` | `wait`, `end` | 시간은 기존 duration 계약을 쓴다. `macro.stop`은 현재 Task를 정리하며 세션 전역 정지는 기존 STOP이 담당한다. |
| `keyboard.tap`, `keyboard.repeat` | `key_tap`, `tap_sequence` | 논리 키와 홀드 시간을 검증한다. 연타는 한 IR 명령으로 내린다. |
| `keyboard.down/up` | 직접 대응 없음 | `timed_batch` 내부 키 에지의 의미는 독립적인 down/up과 다르다. 추가 계약이 확정될 때까지 명시적으로 미지원으로 진단한다. |
| `mouse.click`, `mouse.move_to` | `mouse_click`, `mouse_move_timed` | `profile_id`는 기존 프로파일 참조로 해석한다. 동적 좌표의 신선도와 클릭 재검사를 유지한다. |
| `vision.find`, `vision.wait_for` | `vision_find`, `vision_wait`, `vision_result` | 결과 바인딩은 Task 로컬 `result_var`로 내린다. `center_x/center_y`는 `x/y` 결과 필드에 대응한다. 프로젝트 에셋 ID로 템플릿을 해석하고 timeout·ROI·프로파일·만료를 보존한다. |
| `actions.run`, `spatial.observe`, `navigation.move_to` | 기준 IR에 대응 없음 | 등록 서비스가 없으면 `UNAVAILABLE_SERVICE`, 출력이나 서비스 요구사항이 충족되지 않으면 `MISSING_CAPABILITY`로 진단한다. |

## 범용 서비스 의존성

명세가 예시로 지정한 R-1 Spatial Service의 일반 위치·좌표 관측과 R-2 Navigation Service의 피드백 이동은 현재 구현에서 확인되지 않는다. Action Registry와 Application Pack 등록 경로도 확인되지 않는다. 프로젝트 문서에서 각 R 단계의 독립 인수 기준을 확인할 수 없어 R-0 및 R-3~R-6의 완료 여부는 미확인으로 기록한다. 기존 Perception과 마우스 모션은 각각 영상 검색과 포인터 이동의 실행 경로로 재사용한다.

PY-1은 기본 DSL API를 정적 데이터로 등록하고, 선택 서비스 검증에는 서비스 ID·버전·Capability를 제공하는 테스트 더블을 사용한다. PY-2는 기존 IR과 Fake 출력으로 독립 검증할 수 있다. 범용 서비스의 설치나 실제 장치 연결은 이 두 단계의 진입 조건에 포함하지 않는다.

## 저장과 마이그레이션 결정

1. 스키마 1 프로젝트는 기존 최상위 `workspace`와 에셋·출력 설정을 유지한다. 읽기 시 Blockly 활성으로 해석하며, 파일을 열었다는 이유로 버전을 올리지 않는다.
2. Python 편집본이나 모드 전환 메타데이터가 포함된 파일에는 `schema_version: 2`를 사용한다. `editor.active_mode`와 `editor.python`을 추가하고 기존 `workspace`를 Blockly 원본 위치로 유지한다. v1.3의 스키마 검사에는 2가 거부되므로 Python 초안과 오래된 블록이 섞인 파일의 실행을 막는다.
3. 변환 전 양쪽 편집본을 보존한다. Python 초안은 구문 오류 상태로 저장할 수 있다. 현재 편집본의 컴파일이 성공해야 ARM·START를 검토할 수 있고, 저장한 fingerprint는 로드 후 다시 확인한다.
4. 프로젝트 스키마 버전과 실행 IR 버전을 독립적으로 관리한다. Python을 사용해도 기존 명령으로 표현되면 해당 명령을 지원하는 IR 버전을 사용한다.

## 구현 순서

| 단계 | 변경 지점 | 검증 |
|---|---|---|
| PY-1 | AST allowlist, 타입과 심볼, 정적 API Registry 및 소스 범위 | 허용 선언과 금지 구문, 누락 API 진단 |
| PY-2 | AST를 기존 IR로 변환하고 Validator에 전달 | Fake 출력, 인식 결과, 취소와 실패 |
| PY-3 | 정형 Python 생성 및 지원 IR의 Blockly 렌더링 | [왕복 계약](PYTHON_ROUNDTRIP_CONTRACT.md)에 따른 의미 비교 |
| PY-4 | Python 편집기와 모드 전환·저장 | 초안 보존, 스키마 1 열기, 스키마 2 검증, 현재 소스 일치 검사 |
| PY-5 | 외부 LLM 프롬프트와 선택 서비스 Manifest | 등록 API만 제공, 붙여넣은 소스 재검증 |
| PY-6 | 패키징과 통합 회귀 | 기존 프로젝트 및 출력 경로, Python Dry Run, STOP·ARM·포커스 |

PY-0 기준 회귀 테스트는 `macro-studio`에서 `.\.venv\Scripts\python.exe -m pytest -q`로 실행했다. 기존 테스트 279개가 통과했다. ARM·포커스·STOP은 기존 자동 테스트로 확인했다. 새 DSL과 프로젝트 마이그레이션의 동작 테스트는 해당 구현 단계에서 추가한다.
