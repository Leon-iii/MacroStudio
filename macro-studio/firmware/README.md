# Macro Studio Leonardo 펌웨어

참조 보드는 Arduino Leonardo(ATmega32U4)다. USB CDC에서 명령을 받고 유효한 ARM 세션에서 키보드와 마우스 HID 리포트를 보낸다. 연결이 끊기거나 heartbeat가 1,500ms 동안 오지 않으면 눌린 입력을 해제한다.

프로젝트의 `macro-studio` 디렉터리에서 빌드한다.

```powershell
arduino-cli core update-index
arduino-cli core install arduino:avr@1.8.6
arduino-cli lib install Keyboard@1.0.6 Mouse@1.0.1
arduino-cli compile --fqbn arduino:avr:leonardo --output-dir build/leonardo firmware/MacroStudioLeonardo
```

포트를 목록에서 확인한 뒤 Leonardo에 업로드한다. 아래 명령의 `COM5`는 예시다. 업로드 후 Windows가 다른 COM 번호를 할당하면 목록을 다시 조회한다.

```powershell
python -m pip install -e ".[hid]"
macro-studio devices list
arduino-cli upload --fqbn arduino:avr:leonardo --port COM5 --input-dir build/leonardo firmware/MacroStudioLeonardo
macro-studio devices list
macro-studio devices probe --port COM5
```

`devices list`는 포트 정보를 열거한다. `devices probe`는 지정한 포트만 열어 HELLO와 CAPS를 확인하고 세션을 해제한다.

Windows에서 외부 HID로 워크플로를 실행하려면 IR을 ARM하고, 시작 명령에 대상 창과 포트를 지정한다. `COM5`와 `123456`은 포트와 창 핸들 예시다. 창 핸들은 `macro-studio windows`로 확인한다.

```powershell
macro-studio arm examples/live.json --confirm-real-input
macro-studio start examples/live.json --target 123456 --duration-ms 5000 --hid-port COM5
```

편집기에서는 **실행 설정 → 출력 방식 → External HID**를 선택한다. COM 포트를 선택하고 **연결 확인**이 표시되면 대상 창을 지정한 뒤 ARM을 설정하고 시작한다.

Runner는 실행 중 heartbeat를 보내고 종료할 때 장치의 키와 버튼을 해제한다. 좌표 보정은 최대 32회 또는 600ms로 제한하며, 좌표에 도달하지 못하면 해당 클릭을 취소한다.

펌웨어 0.2.0부터 **HID 실행 → Onboard**를 선택하면 반복 키 입력 블록의 타이밍을 Leonardo가 관리한다. CLI에서는 `--hid-port COM5 --hid-onboard`를 지정한다. Runner는 장치의 완료 응답을 받은 뒤 다음 블록으로 진행하고, 정지 또는 일시정지 시 실행 중인 작업을 취소한다.

펌웨어 0.3.0부터 **시간 → 예약 입력 묶음 (Timed Batch)**으로 입력을 예약할 수 있다. 키, 버튼, 상대 이동 블록의 시각은 묶음 시작 기준 밀리초다. 묶음 하나는 최대 8개 이벤트와 5초까지 지원한다. 마우스 이동과 버튼 입력은 서로 다른 묶음에 배치한다. 드라이런으로 일정을 확인한 뒤 Onboard 모드에서 실입력을 실행할 수 있다. CLI 예제는 `examples/timed-batch.json`에 있다.

바이트 형식과 장치 응답은 [HID Streaming 1.0](../schemas/output_protocol_v1.md)에 정의되어 있다.
