# External HID Streaming 1.0 페이로드

모든 정수는 little-endian이다. 프레임 헤더·COBS·CRC와 opcode 번호는 v1.1 명세 §23을 따른다. 응답에는 요청의 `seq`와 `session_id`를 복사한다.

| 메시지 | 바이트 순서 |
|---|---|
| `HELLO` | `major:u8`, `minor:u8`, `requested_session_id:u32`. 토큰은 0이 아니어야 하고 헤더의 토큰과 같아야 한다. |
| `HELLO_ACK` | `major:u8`, `minor:u8`, 펌웨어 버전의 `major:u8`, `minor:u8`, `patch:u8`, `state:u8`, 끝까지 ASCII 보드 모델명 |
| `CAPS` | `streaming_flags:u8`, `max_payload:u8`, `watchdog_ms:u16`, `max_onboard_jobs:u8`. Streaming 비트 0은 키보드, 1은 마우스 버튼, 2는 상대 이동을 나타낸다. |
| `STATUS` | `state:u8`, `job_id:u16` (0이면 작업 없음), `last_error:u16` (0이면 오류 없음) |
| `INPUT_EVENT` 키 | `kind:u8=1`, `action:u8`, `usage_page:u16`, `usage:u16` |
| `INPUT_EVENT` 버튼 | `kind:u8=2`, `action:u8`, `button:u8` (1 왼쪽, 2 오른쪽, 3 가운데) |
| `INPUT_EVENT` 상대 이동 | `kind:u8=3`, `dx:i16`, `dy:i16` |
| `ACK` | `request_opcode:u8` |
| `NACK` | `request_opcode:u8`, `error:u16` |
| `FAULT` | `error:u16`. 비동기 오류는 헤더의 `seq`를 0으로 두고 영향받은 세션의 토큰을 사용한다. 세션 밖에서는 토큰도 0이다. |

`GET_CAPS`, `ARM_SESSION`, `DISARM_SESSION`, `HEARTBEAT`, `RELEASE_ALL`, `GET_STATUS`에는 요청 페이로드가 없다. 입력 action은 1이 누름, 2가 해제다. 가상 MCU의 키 범위는 usage page `0x07`의 `0x04–0x73`, `0xE0–0xE7`이며 상대 이동은 i16을 받는다. 장치 상태 0, 1, 2는 각각 DISARMED, IDLE, ARMED다.

| 오류 코드 | 의미 |
|---|---|
| 1 | BAD_PAYLOAD |
| 2 | UNSUPPORTED |
| 3 | INVALID_STATE |
| 4 | INVALID_SESSION |
| 5 | SEQUENCE_CONFLICT |
| 6 | INVALID_FRAME |
| 7 | WATCHDOG_EXPIRED |
| 8 | INCOMPATIBLE_VERSION |

`HELLO`가 장치를 IDLE로 전환한다. ARMED 상태에서만 입력을 적용하며 watchdog은 ARM과 heartbeat로 갱신된다. `RELEASE_ALL`과 `DISARM_SESSION`은 눌린 입력을 해제하고 세션을 종료한다. 호스트는 한 번에 요청 하나를 보내고, 타임아웃 이후 새 세션으로 복구한다. 순번 65,535는 종료에 예약하며 다음 세션은 새 토큰을 사용한다.

가상 MCU는 최근 수락한 세션 토큰 32개와 활성 세션의 요청·응답 32쌍을 유지한다. 최근 요청의 동일한 재수신에는 원래 응답을 돌려주며 입력 적용과 heartbeat 갱신은 한 번만 한다. 해제와 종료는 이 요청 기록보다 우선한다.

Leonardo는 최근 세션 토큰 32개를 유지하며 활성 세션에서 직전 승인 요청만 같은 응답으로 재전송한다. 더 낮은 순번은 거절한다. 순번 0은 긴급 `RELEASE_ALL`과 비동기 `FAULT`에 사용할 수 있다. Leonardo 상대 이동은 축당 −127~127을 넘으면 UNSUPPORTED로 세션을 종료한다. 장치는 CDC DTR이 해제되거나 watchdog이 만료되면 입력을 해제한다.

## Onboard TAP_TRAIN

`CAPS.max_onboard_jobs=1`인 펌웨어에서 TAP_TRAIN 작업 하나를 준비하고 실행할 수 있다. 준비 상태에서 입력이 발생하지 않는다. 시작 ACK 이후 Leonardo가 자체 마이크로초 타이머로 누름과 해제를 스케줄한다.

| 메시지 | 바이트 순서 |
|---|---|
| `PREPARE_JOB` | `job_id:u16`, `usage_page:u16=7`, `usage:u16`, `period_us:u32`, `hold_us:u32`, `count:u32` |
| `PREPARE_JOB`의 ACK | `request_opcode:u8`, `job_id:u16` |
| `START_JOB`, `CANCEL_JOB` | `job_id:u16`. ACK는 요청 opcode를 포함한다. |
| `JOB_DONE` | `job_id:u16`, `status:u8` (0 완료, 1 취소, 2 실패), `actual_count:u32`, `error:u16`. 헤더 순번은 0이다. |

Leonardo는 작업 ID 1–65,535, 키 Usage 0x04–0x73, 주기 5,000–1,000,000µs, 주기보다 짧은 양의 누름 시간, 반복 1–1,000,000회를 받는다. 취소 ACK는 진행 중인 키 해제를 확인한다. 완료 카운트는 장치가 수행한 누름 횟수다.

## Timed Batch

펌웨어 0.3.0은 `CAPS.streaming_flags`의 비트 3으로 Timed Batch를 표시한다. `QUEUE_BATCH`(0x0C)는 `job_id:u16`, `event_count:u8`와 고정 길이 이벤트를 받는다. 이벤트 하나의 순서는 `offset_us:u32`, `action:u8`, `a:i8`, `b:i8`이다.

| Action | 의미 | a | b |
|---|---|---|---|
| 1 / 2 | 키 누름 / 해제 | 키 Usage 0x04–0x73 | 0 |
| 3 / 4 | 마우스 버튼 누름 / 해제 | 왼쪽 1, 오른쪽 2, 가운데 3 | 0 |
| 5 | 마우스 상대 이동 | ΔX −127~127 | ΔY −127~127 |

이벤트는 1–8개이며 시작 시각 기준 0–5,000,000µs 범위에서 오름차순으로 작성한다. 같은 시각의 이벤트는 배열 순서를 따른다. 모든 누름은 해제와 짝을 이루어야 한다. 상대 이동과 버튼은 독립된 묶음으로 예약한다.

장치는 전체 페이로드를 검증한 뒤 `ACK`(요청 opcode와 job_id)를 보낸다. `START_JOB` 전에는 이벤트가 실행되지 않는다. `CANCEL_JOB`은 실행을 중단하고 눌린 입력을 해제한다. 완료 응답의 `actual_count`는 실행된 이벤트 수다. 작업 슬롯은 TAP_TRAIN과 공유한다.

`tests/test_hid_codec.py`에 프레임 예제와 오류 벡터가 있다. `tests/test_hid_transport.py`는 호스트와 가상 MCU 사이의 바이트 스트림을 검증한다.
