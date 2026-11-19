# Macro Studio v1.4 Python 사용자 가이드

## 프로젝트와 편집 모드

파일 메뉴에서 기존 `.macroproj`를 열면 블록 모드로 표시된다. 기존 스키마 1 파일은 블록 모드로 저장할 때 스키마 1을 유지한다. Python 편집 자료를 만들면 스키마 2로 저장하며, v1.3에서는 이 파일을 열 수 없다. 원본 파일을 유지하려면 Python 작업 전에 다른 이름으로 저장한다.

편집 영역의 `Python` 탭을 선택하면 현재 블록을 검증한 뒤 코드로 변환한다. `블록` 탭을 선택하면 현재 Python을 검증하고 같은 의미의 블록을 생성한다. 지원하지 않는 블록·문법이나 오류가 있으면 원본을 보존하고 해당 위치를 안내한다.

Python 편집기는 구문 강조, 줄 번호, 현재 위치, 사용 서비스와 검증 결과를 표시한다. 오류 줄을 확인하거나 `오류 위치 보기`를 누르면 해당 소스로 이동한다. `Python → 생성된 Python 보기…`는 정형 코드를 읽기 전용으로 보여 준다.

## Python 드라이런

1. `examples/python-showcase.macroproj`를 연다. 이 예제에는 선언형 데스크톱 액션 팩이 포함되어 있다.
2. 출력 방식이 `드라이런`인지 확인한다.
3. `검증`을 누른다. 성공하면 시작 아이콘이 활성화된다.
4. 시작하면 Enter 누름·해제가 실행 로그에 표시된다. 실제 키보드·마우스에는 입력을 보내지 않는다.
5. 실행 패널의 모의 핫키에 `CTRL+F6`을 입력하고 보내면 A 키 연타가 드라이런 로그에 나타난다.
6. 정지 아이콘을 눌러 별도 Runner를 종료한다.

다음 코드는 조합 핫키가 발생하면 키를 한 번 누른다.

```python
@on_hotkey("CTRL+F6")
async def tap_key():
    await keyboard.tap("A", hold_ms=30)
```

API 인자의 타입과 범위는 [API 레퍼런스](MACRO_STUDIO_PYTHON_API.md)와 내보낸 `api-manifest.json`에서 확인한다. `keyboard.repeat`는 전용 IR 명령을 사용한다. 시간 값은 정수 밀리초다. `macro.stop()`은 현재 Task를 끝내며, 세션 전체 정지는 화면 정지 아이콘이나 긴급 정지 핫키를 사용한다.

## 초안 저장과 복원

구문 오류가 있어도 프로젝트를 저장할 수 있다. 오류가 있거나 수정 후 검증하지 않은 Python은 실행할 수 없다. 저장·재열기 뒤에는 현재 소스와 설정을 다시 검증한다.

블록 변환 전 워크스페이스와 Python 재생성·가져오기 전 소스는 마지막 보존본으로 남는다. `Python → 이전 Python 초안 복원`과 `이전 블록 편집본 복원`은 현재 자료와 마지막 보존본을 교환한다. 이전 전체 이력을 보관하는 기능은 아니다.

실행 중 소스를 수정해도 진행 중인 Runner는 시작한 IR 스냅샷을 사용한다. 수정본은 다음 실행 전에 검증한다. 컴파일 중 편집 내용이나 설정이 바뀌면 이전 결과를 폐기한다.

## 실제 입력과 입력 해제

1. 승인한 테스트 창을 준비하고 대상 창에서 선택한다.
2. 출력 방식을 `SendInput` 또는 `External HID`로 고른다. HID를 사용하면 장치 이름이 표시된 포트를 선택하고 연결 확인이 끝날 때까지 기다린다.
3. 프로젝트를 저장하고 Python을 검증한다.
4. ARM 아이콘을 누르고 실제 입력 허용 질문을 확인한다. ARM 상태는 프로젝트 경로·ID에 연결된 로컬 설정이다. 프로젝트 파일이나 LLM 자료에 포함되지 않는다.
5. 시작한 뒤 안내된 5초 안에 대상 창을 전경으로 전환한다. 마우스 동작은 포인터와 목적지가 대상 창 안에 있어야 한다.
6. 끝낼 때 정지 아이콘을 누른다. 긴급 정지는 `CTRL+ALT+SHIFT+F11`이다.

ARM 전에는 선택한 실제 출력 방식에서도 드라이런으로 실행한다. ARM 설정 자체는 실행을 시작하지 않는다. 포커스를 잃으면 실행을 일시정지하고 세션이 누른 키·버튼과 예약된 입력을 정리한다. 포커스를 되돌려도 자동으로 재개하지 않는다. 재개는 일시정지 아이콘 또는 `CTRL+ALT+SHIFT+F10`으로 명시한다.

HID Runner가 종료되면 포트 연결 상태를 다시 확인할 수 있다. 다음 실입력 시작은 연결 확인이 끝난 뒤 진행한다. 입력 소유권과 충돌 정책은 블록·Python에 공통 적용된다.

## 화면 인식과 마우스 모션

이미지 메뉴에서 템플릿을 등록한 뒤 공개 ID를 사용한다. `vision.find` 또는 `vision.wait_for`의 결과를 변수에 저장하고 `status == "FOUND"`인 분기에서 좌표를 사용한다. Runtime은 만료된 결과와 클릭 전 재검사를 처리한다. 선택한 캡처 화면과 ROI가 대상 창을 포함하는지 확인한다.

```python
@on_start()
async def click_confirmation():
    result = await vision.find("asset:confirm", timeout_ms=1000)
    if result.status == "FOUND":
        await mouse.move_to(result.center_x, result.center_y,
                            profile_id="builtin.smooth", duration_ms=300)
        await mouse.click(hold_ms=30)
```

이 예제의 `asset:confirm`을 프로젝트에 먼저 등록해야 한다. 좌표는 물리 화면 픽셀이다. 화면 배율이 다른 모니터에서도 캡처·모션·대상 창 판정은 이 좌표를 사용한다. HID는 상대 보고서로 이동하므로 OS 포인터 설정에 따라 실제 도착점에 오차가 생길 수 있다.

## 외부 LLM과 선택 서비스

[외부 LLM 작성 절차](LLM_AUTHORING.md)에 따라 작성 자료 ZIP을 내보낸다. 생성 코드는 변경 비교에서 승인한 뒤 초안에 적용하고 검증·드라이런을 거친다. 오류가 나면 `진단 복사`의 JSON을 수정 요청에 사용한다.

`Python → 서비스 Manifest 관리…`에서 등록한 팩은 프로젝트에 저장된다. 현재 `actions.run` v1은 키 누르기·마우스 클릭·대기의 선언형 액션을 제공한다. [Manifest 계약](SERVICE_MANIFEST.md)을 참고한다.

## 오류와 제한

| 코드 | 확인할 내용 |
|---|---|
| `SYNTAX_ERROR`, `UNSUPPORTED_SYNTAX` | 지원하는 DSL 문법과 오류 줄 |
| `UNKNOWN_API`, `INVALID_ARGUMENT`, `TYPE_ERROR` | API 이름·인자 이름·타입·범위 |
| `UNKNOWN_ASSET`, `UNKNOWN_ACTION` | 등록된 템플릿·프로파일·액션 ID |
| `UNAVAILABLE_SERVICE`, `SERVICE_VERSION`, `INVALID_MANIFEST` | 등록 팩·정확한 API 버전·Manifest 계약 |
| `MISSING_CAPABILITY` | 선택한 출력 및 실제 장치의 지원 기능 |
| `SOURCE_LIMIT`, `CALL_DEPTH`, `RECURSIVE_WORKFLOW`, `UNBOUNDED_LOOP` | 소스·호출 깊이·반복 한도 |
| `STALE_SNAPSHOT` | 현재 소스와 프로젝트 설정의 재검증 |

소스는 최대 65,536자·4,096 AST 노드다. 워크플로 반환값, 독립 키 down/up, 임의 Python import·파일·네트워크 접근, Spatial·Navigation 서비스는 지원하지 않는다. Blockly 왕복은 [공통 부분집합](PYTHON_ROUNDTRIP_CONTRACT.md)을 지원하며 사용자 이벤트·상태 머신·일부 기존 센서·Timed Batch 등의 변환은 거부한다.

Windows 배포 ZIP을 전체 압축 해제하고 `MacroStudio.exe`를 실행한다. `_internal` 폴더를 함께 유지한다. Python 설치는 필요하지 않다.
