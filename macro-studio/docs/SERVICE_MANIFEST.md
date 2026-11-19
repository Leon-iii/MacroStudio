# Service Manifest 버전 1

프로젝트의 `service_manifests` 배열은 선언형 Application Pack을 보관한다. `service_requirements`는 필요한 서비스의 정확한 API 버전을 저장한다. 현재 지원하는 선택 서비스는 `actions.run` 버전 `1`이다. Manifest의 Python 코드·모듈·플러그인 경로 필드는 허용하지 않는다.

## 계약

최상위 필드는 `manifest_version`, `application_pack`, `services`다. `manifest_version`은 정수 `1`이다. `application_pack`에는 `id`, `name`, `version`이 필요하며 현재 팩 버전은 문자열 `"1"`이다.

`services`에는 다음 계약의 서비스 하나가 들어간다.

```json
{
  "service_id": "actions.run",
  "api_version": "1",
  "python_symbol": "actions.run",
  "kind": "async_action",
  "parameters": [{"name": "action_id", "type": "String", "required": true}],
  "returns": "None",
  "ir_opcode": "repeat",
  "required_capabilities": [],
  "interruptible": true,
  "actions": []
}
```

실제 Manifest에는 액션 1~64개가 필요하다. 각 액션의 필드는 `id`, `name`, `required_capabilities`, `steps`다. 액션과 팩 ID는 영문 소문자로 시작하며 소문자·숫자·점·밑줄·하이픈으로 구성한 최대 96자다. 이름은 공백이 아닌 최대 128자 문자열이다. 액션 ID는 프로젝트 전체에서 고유해야 한다.

명령은 다음 필드만 허용한다. 시간은 정수 나노초이며 기존 IR Validator의 시간·키·버튼 검사를 적용한다. 액션의 합계 시간은 24시간 이하다.

| 명령 | 필드 | 요구 기능 |
|---|---|---|
| `key_tap` | `op`, `key`, `hold_ns` | `output.keyboard` |
| `mouse_click` | `op`, `button`, `hold_ns` | `output.mouse` |
| `wait` | `op`, `duration_ns` | 없음 |

`required_capabilities`는 명령에서 구한 요구 기능과 일치해야 한다. 액션당 명령은 1~64개, 프로젝트당 팩은 최대 8개, Manifest 하나는 JSON 직렬화 기준 128 KiB 이하다.

## 컴파일과 실행

`await actions.run("desktop.confirm")`의 액션 ID는 문자열 리터럴이다. Registry는 프로젝트에 등록된 ID만 해석한다. 해당 액션의 명령을 소스맵이 있는 반복 1회 IR로 변환하고 전체 프로그램을 기존 Validator와 출력 기능 검사에 통과시킨다. 실행 시 Runner의 키 소유권·충돌·STOP·해제 경로가 적용된다.

서비스가 없으면 `UNAVAILABLE_SERVICE`, 필수 버전이 다르면 `SERVICE_VERSION`, 액션 ID가 없으면 `UNKNOWN_ACTION`, 출력 기능이 맞지 않으면 `MISSING_CAPABILITY`를 반환한다. 잘못된 Manifest는 `INVALID_MANIFEST`다. 등록한 팩은 프로젝트와 함께 저장되며 컴파일 시 다시 검증한다. Manifest 변경은 이전 실행 스냅샷을 무효화한다.

Blockly 전환 시 액션은 기존 입력·대기 블록으로 펼쳐질 수 있다. 원래 Python 초안은 보존된다. `spatial.observe`와 `navigation.move_to` 및 새 Runner opcode를 요구하는 서비스는 현재 등록 범위에 포함되지 않는다. JSON에 해당 심볼을 적는 것만으로 호출 권한이 생기지 않는다.

[데스크톱 예제 팩](../src/macro_studio/authoring/desktop-actions.json)은 Enter, Esc와 짧은 대기를 제공한다. 인식 서비스나 게임별 기능에 의존하지 않는다. 프로젝트 컨텍스트에는 팩·서비스·액션의 공개 ID·이름·요구 기능을 내보낸다. 사용자 Manifest의 동작 명령은 기본 컨텍스트에 포함하지 않는다.
