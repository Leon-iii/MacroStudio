# Phase 0 IR v1

파일은 UTF-8 JSON입니다. 최상위 필드는 `ir_version`(정수 1), `project_id`(비어 있지 않은 문자열), `workflows`(객체), `handlers`(배열)입니다. `globals`는 전역 변수 이름과 초기값을 담는 객체이며 기본값은 빈 객체입니다.

이미지 인식 블록이 있는 프로젝트는 `ir_version: 3`을 사용합니다. 기존 IR 1과 모션 프로파일의 IR 2도 읽습니다.

## IR 3 시각 인식

- `vision_find`와 `vision_wait`는 `spec`, `block_id`, `result_var`, `timeout_ms`를 사용합니다. `vision_wait`는 `condition`에 `appear` 또는 `disappear`를 받습니다. `timeout_ms: null`은 무한 대기를 뜻하며 STOP으로 취소할 수 있습니다.
- `spec.detector`는 `template` 또는 `hsv`입니다. 템플릿 검색에는 `spec.image`가 필요합니다. `spec.roi`의 `left`, `top`, `width`, `height`는 캡처 소스 기준 물리 픽셀입니다.
- `vision_visible`은 같은 검출 설정으로 최신 관측을 검사하는 불리언 식입니다. `vision_result`는 `result_var`와 `field`로 같은 Task의 `x`, `y`, `score`, `status`, `error_code`, `pixel_count`, `fraction`을 읽습니다.
- `event.type`이 `on_image_appeared` 또는 `on_image_disappeared`인 핸들러는 감시할 `spec`을 갖습니다. 우선순위와 재진입은 일반 이벤트 핸들러와 같습니다.

## 워크플로와 핸들러

각 워크플로는 이름으로 참조하며 `steps` 배열을 가집니다. 각 핸들러는 고유한 `id`, `workflow_id`, `event`를 가집니다. 이벤트는 `{"type":"project_start"}` 또는 `{"type":"hotkey_pressed","hotkey":"F7"}`입니다. 선택 필드는 불리언 식 `guard`, 정수 `priority`(기본값 0), 불리언 `enabled`(기본값 true), `policy`입니다. 정책은 `ignore_if_running`(기본값), `restart`, `queue`, `parallel`, `override`를 지원합니다. 우선순위는 숫자가 클수록 높습니다.

## 명령

각 명령은 `op`와 아래 필드를 가집니다. 선택 필드 `block_id`로 오류를 원본 블록에 연결합니다.

| `op` | 필드 |
| --- | --- |
| `wait` | `duration_ns`, 0 이상의 정수 |
| `repeat` | `count`(0~1,000,000 정수로 평가되는 식), `steps` |
| `forever` | `steps` |
| `if` | `condition`(불리언 식), `then`, 선택적 `else` |
| `set` | `var`, `value`(변수 타입과 일치하는 식) |
| `call` | `workflow_id` |
| `end` | 현재 Task 종료 |
| `key_tap` | `key`, 양의 정수 `hold_ns` |
| `tap_sequence` | `key`, 정수 `frequency_hz`(1~200), 양의 정수 `hold_ns`, 정수 `count`(1~1,000,000), 선택적 `missed_tick_policy`(`skip_missed`) |

연타의 누름 시간은 주기보다 짧아야 합니다. 키는 A~Z, 0~9, F1~F12, ENTER, SPACE, TAB, ESC 또는 `{"kind":"scan_code","code":30,"extended":false}`로 지정합니다. 스캔 코드는 1~127입니다. `extended`의 기본값은 false입니다. 같은 키는 동일한 로그 키로 변환됩니다. 예를 들어 A와 스캔 코드 30은 `scan:1E`입니다.

## 식과 로그

식은 유한한 숫자, 불리언, 문자열, `{"var":"enabled"}` 또는 `{"op":"add","args":[1,2]}` 형태입니다. 산술은 `add`, `sub`, `mul`, `div`를 지원합니다. 비교는 `eq`, `ne`, `lt`, `le`, `gt`, `ge`를 지원합니다. 논리는 `and`, `or`, `not`을 지원하며 `not`만 인자 하나를 받습니다. 전역 변수의 초기값이 타입을 정합니다.

`run`은 키 이벤트 `events`, 최종 전역 변수 `globals`, 실행 오류 `errors`, 생략한 연타 주기 수 `missed_ticks`를 JSON으로 출력합니다. 종료 코드는 성공 0, 런타임 오류 1, 잘못된 명령 또는 IR 2입니다.
