# v1.3 시각 인식 구현 범위

기준은 MacroStudio v1.2의 `f5258e9`다. [v1.3 명세](../../MacroStudio_Visual_Recognition_Spec_v1_3.md)는 이 문서의 기능·검증 기준이다.

| 단계 | v1.2에서 재사용하는 코드 | 추가할 동작 | 검증 |
|---|---|---|---|
| VR-0 | `ScreenSensors`의 작업 프로세스와 bounded 큐 패턴 | 프레임, 물리 픽셀 ROI, 결과, 상태, 출현·소멸의 데이터 계약 | 입력 없이 공급하는 가짜 프레임 |
| VR-1 | 기존 Windows DPI 인식과 물리 좌표 출력 | MSS 모니터 열거, 캡처, 프레임 수명, 실패 상태 | BGR 채널, 음수 원점, 캡처 중지와 재시작 |
| VR-2 | 프로젝트에 저장되는 이미지 에셋 ID와 참조 검사 | OpenCV 템플릿과 HSV, 검색 결과 좌표, 프로파일 우선순위 및 저장 | 위치·점수, HSV 경계, 손상 이미지와 경로, 설정 왕복 |
| VR-3 | 기존 Task 스케줄러, 우선순위·재진입, STOP·일시정지 정리 | 별도 Perception Worker 요청·구독·만료, 안정화와 에지 이벤트 | 중복·UNKNOWN·재시작, 동시 작업과 STOP |
| VR-4 | Blockly, Compiler, Validator, `mouse_move_timed`, Output Router | 탐색·대기·감시, 결과의 X·Y·Score·Status, 오래된 좌표에서 입력 차단 | Fake 입력의 찾기·이동·클릭과 기존 프로젝트 회귀 |
| VR-5 | 이미지 에셋 관리와 PySide6 메뉴 스타일 | 캡처 소스와 ROI 선택, 프로파일 관리, 미리보기 | 앱에서 저장·취소, 오류와 오버레이, 메뉴와 블록 유효성 |
| VR-6 | Windows 배포와 기존 Fake/HID 테스트 | 인식 진단, 매핑 문서, 사용 예제 | 전체 회귀, Windows에서 캡처와 화면 확인 |

v1.2 화면 센서는 위치를 반환하지 않는 픽셀·이미지·텍스트 불리언 식이다. 별도의 인식 결과가 필요한 새 블록은 시각 인식 인터페이스를 사용한다. 기존 센서 프로젝트는 기존 식을 계속 해석한다. Perception Worker가 프레임과 관측을 소유하고, Runner가 Task와 입력을 소유한다. UI 미리보기는 Runner의 입력 예약 큐를 거치지 않는다.

실기기를 분리하거나 다시 연결하는 검증은 이번 작업에서 제외한다. 미연결 및 출력 경로 실패는 가짜 장치로 확인한다. TensorFlow 룬 판독은 [v1.4 후속 명세](../../MacroStudio_Rune_Recognition_Spec_v1_4.md)를 따른다.
