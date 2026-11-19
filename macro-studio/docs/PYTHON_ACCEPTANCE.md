# v1.4 Python 통합 인수 검증

검증일은 2026-11-19다. Python DSL을 공통 IR과 기존 별도 Runner로 연결한 PY-0~PY-6 범위를 확인했다. Spatial·Navigation과 워크플로 반환값 등은 알려진 제한으로 명시한다.

## 자동 검사

| 인수 범위 | 증거 |
|---|---|
| PY-T01~T13 선언·타입·구문·API·자원 한도 | `test_python_dsl.py`, `test_python_compiler.py`, `test_python_authoring.py` |
| PY-T14~T16 인식 결과·오류·신선도 | Python 컴파일러의 FOUND/NOT_FOUND/UNKNOWN·오래된 좌표 검사와 `test_perception.py`, 인식 Runtime 회귀 |
| PY-T17~T19, T23~T24 왕복·결정성·소스맵 | `test_python_roundtrip.py`, `test_python_acceptance.py`, GUI 재열기 검사 |
| PY-T20~T22, T34~T35 초안·이전 스냅샷·구버전·비동기 편집 | `test_python_editor.py` |
| PY-T25~T28 출력 기능·Fake·ARM·STOP·포커스 | `test_python_authoring.py`, `test_output_routing.py`, `test_live.py` 및 아래 실기기 검사 |
| PY-T29~T33 한도·선택 서비스·외부 작성·입력 동등성 | 호출 깊이·소스 크기·이벤트 정책 회귀, 서비스 격리, 예제 8종과 왕복 Fake 출력 검사 |

기존 스키마 1 예제 5종을 실제 GUI의 열기·저장 경로로 검사했다. 워크스페이스, 이미지 에셋, 전역 변수, 프로파일·출력 설정과 컴파일 IR이 저장 전후 동일했다. Python 스키마 2의 초안·Manifest·필수 서비스 버전·생성 소스맵은 별도 저장·재열기 검사로 확인했다.

전체 `pytest -q` 결과는 378개 통과다. Auto Maple의 Python 모듈이나 리소스를 가져오지 않고 실행했다.

## Windows 및 Leonardo

Windows 11 x64에서 전용 `Macro Studio Python Acceptance Target` 창을 생성했다. 사용자 작업 창에는 매크로를 보내지 않았다. `tools/verify-python-windows.py --confirm-real-input --hid-port COM5 --report build/python-windows-acceptance.json`로 재실행할 수 있다.

| 사례 | SendInput | Leonardo COM5 |
|---|---|---|
| ARM 전 Python 실행 | 실제 입력 0건, 드라이런 | 실제 입력 0건, 드라이런 |
| 명시적 ARM만 설정 | 자동 실행 없음 | 자동 실행 없음 |
| A 키·왼쪽 버튼을 실제로 누른 상태에서 STOP | 두 입력 해제·Runner 종료 | 두 입력 해제·Runner 종료 |
| 동일 상태에서 포커스 이탈 | 두 입력 해제·일시정지 | 두 입력 해제·일시정지 |
| 대상 창으로 포커스 복귀 | 자동 재개 없음 | 자동 재개 없음 |
| 긴급 정지 CTRL+ALT+SHIFT+F11 | 두 입력 해제·Runner 종료 | 두 입력 해제·Runner 종료 |

실제 화면에서 전용 창의 텍스트 패치를 캡처해 `vision.find → mouse.move_to → mouse.click`을 실행했다. 드라이런에서는 실제 입력과 포인터 이동이 없었고, SendInput에서는 지정한 테스트 창이 클릭을 받았다. 두 경로 모두 정상 종료했으며 눌린 입력이 남지 않았다. 혼합 DPI 캡처는 물리 좌표와 선택 모니터의 원점을 사용했다.

PY-M01~M05의 탭 전환·오류 표시·작성 자료·출력 선택은 PY-4/PY-5의 실제 Qt WebEngine 화면과 GUI 회귀에서 확인했다. PY-M06~M07은 위 실기기 검사로 확인했다. 최소 960×640 화면도 확인했다.

## 인수 중 수정한 사항

- HID 좌표 클릭의 정적 Capability가 실제 `HidBackend`와 다르게 차단하던 부분을 맞췄다. 상대 HID를 사용한 좌표 동작과 실제 장치의 기능 검사는 기존 백엔드 경로를 따른다.
- `WindowsDesktop`의 포인터 조회·설정, 목적지 판정과 절대 이동을 물리 좌표 DPI 컨텍스트로 통일했다. 호출 스레드가 DPI 비인식 상태인 경우의 회귀 검사를 추가했다.

## 컴파일 측정

Windows 11, Python 3.13.1, 논리 CPU 12개에서 각 규모를 한 번 준비한 뒤 7회 측정했다. 측정 전 시스템 CPU 사용률 표본은 약 31.8%였다. 입력 명령 수와 opcode를 매번 확인했고 실패는 0건이었다.

| 입력 명령 | 소스 길이 | 중앙값 | 최소~최대 |
|---|---|---|---|
| 10개 | 430자 | 0.452 ms | 0.403~0.735 ms |
| 100개 | 4,030자 | 3.085 ms | 2.570~3.930 ms |
| 300개 | 12,030자 | 9.045 ms | 8.314~10.502 ms |

측정 범위는 파싱·IR 생성·검증·fingerprint 재확인이다. 파일 I/O·UI 표시·입력 실행은 포함하지 않는다. 별도 프로파일 실행의 주요 비용은 Python AST 파싱·순회와 IR 검증이었다. 기존 버전 대비 성능 향상을 주장하는 측정은 아니며, 공유 데스크톱의 부하와 스케줄링 영향을 받는다. GUI 검증은 작업 스레드에서 수행하며, 컴파일 중 편집과 이전 결과 폐기 검사를 별도로 통과했다. 입력 타이밍의 하드 실시간 보장을 의미하지 않는다.

원시 기록은 [Windows 실기기 결과](acceptance/python-windows-2026-11-19.json)와 [컴파일 측정](acceptance/python-compile-2026-11-19.json)에 보관한다.

## 배포 검사와 한계

Windows x64 onedir 배포본에는 Python Runtime, Blockly, 인식 의존성, 작성 자료와 사용자 가이드를 포함한다. 패키지 검사는 기본 Blockly·마우스 모션·v1.3 인식 프로젝트와 v1.4 Python 프로젝트의 열기·저장·검증을 확인한다. Python 예제의 별도 Runner 드라이런과 작성 자료 ZIP 생성도 검사한다.

이 호스트에는 개발용 Python이 설치되어 있다. 배포 검사는 실행 프로세스의 PATH에서 Python을 제거하고 PYTHONPATH/PYTHONHOME을 지운 상태로 시작하며, 편집기와 Runner가 패키지 내부 Python DLL을 로드하는지 확인한다. Python이 전혀 설치되지 않은 별도 Windows 설치본 검증은 수행하지 않았다. USB 분리·watchdog·장시간 주파수 검증은 기존 하드웨어 보고서의 범위이며 이번 PY-6에서는 재수행하지 않았다.
