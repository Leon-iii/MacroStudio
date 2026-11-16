# 마우스 모션 프로파일 구현 차이

기준은 저장소 루트의 [v1.2 통합 명세 Part C](../../MacroStudio_Codex_Implementation_Spec_v1_2_Integrated.md#part-c-v12-mouse-motion-profile-system-규범-사양)다. 명세 C0와 C17이 참조하는 `docs/SPEC.md`와 `docs/MOUSE_MOTION_SPEC.md`는 현재 저장소에 없다. 기존 멀티 출력 계약은 [v1.1 멀티 백엔드 명세](../../MacroStudio_Codex_Implementation_Spec_v1_1_Multi_Backend.md)와 [구현 현황](MULTI_BACKEND_GAP.md)에서 확인한다.

## 현재 동작과 후속 작업

| 영역 | 현재 코드 | v1.2 적용 지점 |
|---|---|---|
| 편집기 | 마우스 팔레트에 좌표, 목적지·시간 이동, 좌표 클릭, 버튼 클릭이 있다. 목적지와 지속 시간은 각각 좌표와 시간 블록을 사용한다. 메뉴바에는 파일과 이미지 메뉴가 있다. [Blockly 블록](../src/macro_studio/editor/editor.js#L212) · [메뉴바](../src/macro_studio/gui.py#L269) | MM-2에서 상단 마우스 메뉴와 프로파일 관리 창을 연결한다. MM-3에서 2D 궤적과 속도 그래프를 그리며, 미리보기 재생은 화면 내부의 가상 커서로 표시한다. MM-4에서 절대·상대 이동 블록과 프로젝트 기본값을 따르는 프로파일 선택을 연결한다. |
| 컴파일·IR | `ms_mouse_move`는 절대 `position`과 `duration`을 포함한 `mouse_move`로 컴파일된다. 시간 단위는 ms, sec, min이고 평가 결과는 0까지 허용한다. `mouse_click_at`도 절대 위치를 받는다. Timed Batch의 상대 이동은 개별 정수 델타 이벤트다. [컴파일러](../src/macro_studio/compiler.py#L110) · [IR 검증](../src/macro_studio/ir.py#L210) | MM-4에서 `mouse_move_timed`에 절대·상대 목적지와 프로파일 참조를 도입한다. 기존 `mouse_move`의 목적지와 지속 시간 및 0시간 경로를 보존한다. |
| 프로젝트 저장 | 편집기는 `.macroproj` 스키마 1을 읽고 `QSaveFile`로 저장한다. `output_profile`은 출력 종류, HID 실행 방식, USB 장치 선택 정보를 저장한다. ARM 승인은 별도의 로컬 데이터에 프로젝트 ID와 경로를 연결해 저장한다. [프로젝트 저장](../src/macro_studio/gui.py#L775) · [출력 프로필](../src/macro_studio/gui.py#L601) · [ARM 저장](../src/macro_studio/live.py#L39) | MM-2에서 프로파일 등록과 기본값 저장을 연결하고, MM-4에서 프로젝트가 참조하는 사용자 프로파일 스냅샷과 기존 프로젝트 호환을 구현한다. 모션 프로파일에는 COM 포트와 출력 채널을 넣지 않는다. |
| 스케줄링·안전 | 양수 지속 시간의 이동은 시작 시 커서 위치를 읽고 16ms 간격의 예약 시각으로 직선 보간한다. 포인터와 버튼 소유권이 충돌하면 오류를 내고, 정지·일시정지 등으로 작업을 없앨 때 소유권과 눌린 입력을 정리한다. 실입력은 ARM 및 대상 창의 포커스 조건을 거친다. [이동 및 소유권](../src/macro_studio/runtime.py#L514) · [작업 정리](../src/macro_studio/runtime.py#L344) · [실행 가드](../src/macro_studio/live.py#L178) | MM-1에서 순수 생성기가 논리 궤적을 만들고, MM-4에서 기존 스케줄러와 연결한다. 이동을 시작할 때 좌표와 프로파일 스냅샷을 얻는다. 취소하면 예약된 포인트를 폐기한다. |
| Fake·드라이런 | Fake는 논리 커서 위치와 이동 이벤트를 기록한다. 편집기 드라이런은 좌표를 실행 로그로 전달한다. [Fake 백엔드](../src/macro_studio/backend.py#L34) · [편집기 Runner](../src/macro_studio/editor_runner.py#L23) | MM-4에서 프로파일 이동의 이벤트 시각과 좌표를 Fake로 검증한다. |
| Windows 출력 | Windows 백엔드는 포인터 절대 이동에 `SetCursorPos`를 사용한다. 현재 커서는 `GetCursorPos`로 읽는다. [Windows 포인터 API](../src/macro_studio/win32.py#L173) | MM-4에서 C6.1의 SendInput 절대 좌표 변환과 가상 데스크톱·음수 좌표·DPI 계약을 검증한다. 레거시 이동에는 현재 동작을 적용한다. |
| External HID·Adaptive | 호스트가 관측한 현재 커서와 목표점의 차이를 상대 이동으로 보낸다. 한 보고서의 축별 범위는 ±127이며, 현재 백엔드는 그 범위로 제한한다. 런타임은 마지막에 관측 위치를 읽어 제한된 횟수로 도착 보정을 시도한다. Adaptive의 장치 위임 후보는 키 연타와 키 전용 Timed Batch다. [HID 백엔드](../src/macro_studio/external_hid/backend.py#L183) · [도착 보정](../src/macro_studio/runtime.py#L550) · [Adaptive 판정](../src/macro_studio/adaptive.py#L30) | MM-5에서 프로파일 궤적의 상대 이동을 양자화하고 잔차를 이월한다. 보고서 분할과 이동량 검증을 추가한다. 장치 예약 실행에는 궤적 기능을 나타내는 별도 capability가 필요하다. |

## 레거시 호환 기준

C3.4와 C9에서 기존 `mouse_move`를 즉시 이동으로 설명하지만, 현재 편집기는 시간 입력이 달린 블록을 생성하고 기존 IR은 양수 지속 시간과 0을 모두 받아들인다. 따라서 새 프로파일 이동에는 새 opcode를 쓰고, 레거시 `mouse_move`는 양수 시간일 때 직선 보간과 최종 위치 처리를 유지한다. 시간값을 0으로 평가하면 중간 보간 없이 최종 이동 경로로 들어간다. External HID에서는 이 경로에서도 제한된 도착 보정이 수행될 수 있다. `Direct` 프리셋의 기본 곡선은 Smoothstep이므로, 기존 동작을 보존하려면 기존 블록과 IR을 레거시 경로에서 실행한다. [블록과 기본값](../src/macro_studio/editor/editor.js#L212) · [이동 코드](../src/macro_studio/runtime.py#L514)

## 검증과 단계 경계

기존 자동 테스트는 Fake에서 시간 이동 중 핫키 처리와 종료점, 정지 후 클릭 해제와 이동 재시작을 검증한다. 라이브 테스트는 대상 창 밖 클릭 거부와 모의 HID 도착 보정 및 USB 연결 해제 정리를 검증한다. Adaptive 테스트는 포인터 Timed Batch를 호스트에서 수행하는 경로에 대상 창 검사가 적용되는지 확인한다. [Fake 이동](../tests/test_mouse_window.py#L65) · [라이브 가드](../tests/test_live.py#L89) · [Adaptive](../tests/test_adaptive.py#L207)

MM-0의 산출물은 이 문서, 순수 프로파일·좌표·미리보기 데이터 모델, v1 JSON 스키마와 검증, 내장 프리셋 5종이다. 궤적 계산은 MM-1, 관리 UI는 MM-2, 가상 커서 미리보기는 MM-3, 기존 프로젝트 호환과 런타임 연결은 MM-4, HID 양자화와 장치 검증은 MM-5에서 각각 검증한다.

MM-1의 `MotionGenerator`는 속도와 드리프트에 독립 난수 스트림을 사용한다. 속도 지터가 0이면 Smoothstep을 직접 계산한다. 전체 횡방향 편차는 이동 거리의 10%로 제한한다. 시작점과 목표점이 같으면 시각 0의 점 하나를 반환한다. [궤적 생성기](../src/macro_studio/mouse_motion/generator.py) · [궤적 검증](../tests/test_mouse_motion_generator.py)

MM-2에서 상단 **마우스** 메뉴로 앱 기본값과 프로젝트 기본값을 설정한다. 첫 실행 시 앱 기본값은 Natural이고, 프로젝트는 기본적으로 앱 설정을 따른다. **모션 프로파일 관리**에서 사용자 프로파일을 생성, 복제, 수정, 삭제한다. 앱 또는 현재 프로젝트의 기본값으로 사용 중인 프로파일을 삭제하려면 먼저 해당 선택을 바꾼다. 프로젝트에서 사용자 프로파일을 선택하면 해당 버전을 프로젝트에 함께 저장한다. 로컬 프로파일을 편집한 뒤에는 프로젝트 메뉴의 **최신 버전 적용**으로 저장본을 바꿀 수 있다. [프로파일 저장](../src/macro_studio/mouse_motion/registry.py) · [관리 창](../src/macro_studio/mouse_profile_dialog.py)

MM-3에서 **마우스 → 궤적 미리보기**는 현재 적용되는 기본 프로파일을 연다. 관리 창에서는 선택한 프로파일을 검사할 수 있다. 2D 궤적과 종방향·전체 속도는 같은 생성 결과를 사용한다. 시드, 좌표, 시간, 샘플링 변경은 미리보기 안에서만 적용된다. 가상 커서는 Qt에서 그리며 Windows 입력을 보내지 않는다. [미리보기 창](../src/macro_studio/mouse_motion_preview.py) · [미리보기 테스트](../tests/test_mouse_motion_preview.py)

MM-4에서 마우스 팔레트에 프로파일을 지정할 수 있는 절대·상대 이동 블록을 추가한다. 프로젝트에서 직접 참조한 사용자 프로파일은 저장할 때 스냅샷으로 포함한다. 새 블록은 드라이런과 Windows SendInput을 지원한다. 기존 블록은 같은 직선 보간 및 출력 경로를 사용한다. 중간에서 정지하면 마지막 전송 위치에 커서가 남는다. [블록 정의](../src/macro_studio/editor/editor.js) · [런타임 테스트](../tests/test_profiled_mouse_move.py)

MM-5에서 상대 이동을 지원하는 Leonardo에 프로파일 궤적을 Streaming 또는 Adaptive로 보낸다. 호스트는 논리적 누적 변위와 이미 전송한 정수 변위의 차이를 다음 보고서에 이월한다. 보고서 간격은 최소 8 ms로 두고, 밀린 중간 점을 합친다. 축당 ±127을 넘는 변위는 분할한다. 정상 종료할 때 논리적 최종 변위를 모두 보낼 때까지 기다리므로 큰 마지막 변위는 블록의 표시 시간을 넘길 수 있다. Windows 포인터 설정에 따라 실제 도착 좌표는 달라질 수 있다.

프로파일 이동은 장치의 Onboard 모드에서 지원 여부를 명시적으로 검사한다. 현행 릴리스는 호스트 스트리밍을 사용하며, 짧은 예약 궤적의 변환은 버퍼와 간격을 검사하는 계약 및 펌웨어 스텁에서 검증한다. [HID 스케줄링 테스트](../tests/test_profiled_hid_motion.py) · [예약 작업 계약](../src/macro_studio/external_hid/trajectory_batch.py) · [펌웨어 스텁 테스트](../tests/test_hid_firmware.py)
