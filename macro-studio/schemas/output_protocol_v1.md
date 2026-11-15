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

`tests/test_hid_codec.py`에 프레임 예제와 오류 벡터가 있다. `tests/test_hid_transport.py`는 호스트와 가상 MCU 사이의 바이트 스트림을 검증한다.
