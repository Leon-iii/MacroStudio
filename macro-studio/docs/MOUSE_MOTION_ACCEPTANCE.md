# v1.2 마우스 모션 인수 검증

## 검증 결과

| 항목 | 결과 |
|---|---|
| 혼합 DPI·다중 모니터 | Windows에서 1920×1080 모니터 두 대를 각각 100%(96 DPI), 125%(120 DPI)로 두고 SendInput으로 경계를 양방향으로 넘었다. Windows가 관측한 도착 좌표는 순서대로 (1740, 540), (2100, 540), (1740, 540)이었고 모두 목표와 일치했다. 같은 배율의 두 모니터에서도 같은 경로를 확인했다. |
| 이동 충돌과 Override | 드라이런에서 경쟁 이동은 `input_conflict`로 거부됐고 원래 이동은 완료했다. Override는 기존 이동을 취소하고 마지막으로 관측된 위치에서 상대 이동을 시작했다. |
| 이동 중 프로파일 변경 | 이동 중 프로젝트 프로파일 시간을 120 ms에서 30 ms로 바꿨다. 진행 중인 이동은 120 ms 스냅샷을 사용했고 다음 실행은 30 ms를 사용했다. |
| 프로젝트 이식성 | 사용자 프리셋 파일이 없는 새 설정 디렉터리에서 프로젝트를 열었다. 앱에는 Natural만 기본으로 적용됐고, 프로젝트는 저장된 사용자 프로파일과 값을 복원했다. |
| Leonardo 포커스 이탈 | 실제 COM5 세션에서 테스트 창이 포커스를 잃자 Runner가 일시정지했다. 이후 추가 상대 이동 보고서는 없었고 커서가 정지 위치에 남았다. |
| Leonardo USB 분리 | COM5에서 상대 이동 중 실제 USB 케이블을 뽑았다. 분리 전 이동 보고서 21개가 ACK를 받았고, 다음 보고서에서 전송에 실패했다. Runner는 `input_error`로 종료했고 Windows 포트 목록에서 COM5가 사라졌다. 실패 뒤 추가 ACK는 없었고 커서는 (1726, 540)에 600 ms 동안 그대로 있었다. |
| 프로젝트 예제 | 앱 기본값과 별개로 프로젝트 Precision 및 블록 Direct를 해석한다. 드라이런에서 예상 좌표에 도달했다. |

[이동·충돌 테스트](../tests/test_profiled_mouse_move.py) · [HID 오류 테스트](../tests/test_profiled_hid_motion.py) · [Windows 실기기 검증 스크립트](../tools/verify-mouse-motion-windows.py)

## Windows 성능 계측

Windows의 Qt 플랫폼에서 Natural 프로파일을 사용했다. 설정 세 가지를 번갈아 5회 측정했다. 렌더 시간은 그래프의 경로 캐시를 비운 뒤 대화상자를 한 번 그리는 시간이다. 타이머 간격은 10 ms로 예약한 Qt 타이머의 관측 간격이며, 계산 대기와 첫 화면 갱신을 포함한다. 모든 실행에서 미리보기가 생성기의 궤적과 일치했다.

| 궤적 점 수 | 생성 시간 중앙값 (범위) | 미리보기 다시 그리기 중앙값 (범위) | 실행별 최대 타이머 간격의 중앙값 (범위) |
|---:|---:|---:|---:|
| 71 | 1.92 ms (0.49–2.33) | 4.30 ms (4.10–4.83) | 11.02 ms (10.24–14.07) |
| 2,001 | 4.64 ms (4.07–5.31) | 21.17 ms (19.47–21.75) | 25.90 ms (21.44–27.14) |
| 60,001 | 122.74 ms (119.55–135.42) | 39.92 ms (39.16–42.97) | 37.85 ms (16.86–46.24) |

실입력은 Windows SendInput과 Leonardo Streaming을 한 번씩 번갈아 각각 5회 실행했다. Direct 프로파일의 350 ms, 200 Hz 상대 이동으로 매번 71점을 생성했다. 정수 좌표가 실제로 바뀌는 예정 점은 66개였다. 생략 수는 이 66개 중 자신의 예정 시각으로 전송되지 않은 점의 수다. 최종 위치 확인용 중복 보고서와 분할 보고서는 별도로 전송 수에 포함될 수 있다.

| 채널 | 전송 호출 수 | 생략된 예정 점 | 세션 시간 중앙값 (범위) | 제출 오차 P50 / P95 / P99 | HID 응답 P50 / P95 / P99 |
|---|---:|---:|---:|---:|---:|
| SendInput | 67 | 0 | 350.91 ms (350.85–351.28) | 0.30 / 0.55 / 0.67 ms | 해당 없음 |
| Leonardo Streaming | 35 | 33 | 394.30 ms (393.62–399.52) | 1.87 / 4.97 / 5.02 ms | 1.65 / 1.87 / 1.89 ms |

백분위는 각 실행에서 먼저 계산한 뒤 실행 간 중앙값으로 요약했다. 제출 오차는 예정 시각에서 호스트의 입력 호출 직전까지의 시간이다. HID 응답은 CDC 명령 전송에서 ACK 수신까지다. Windows가 HID 입력을 처리한 시각은 이 수치에 들어 있지 않다. 측정에 포함된 10회에는 실행 오류가 없었다. SendInput의 도착 좌표와 Leonardo의 전송 변위 합계를 각각 확인했다. Streaming은 보고서 응답 후 최소 8 ms 간격을 적용하므로, 5 ms 간격으로 생성된 일부 점을 합쳐 보낸다.

## 재현

프로젝트 디렉터리에서 실행한다. 하드웨어 검증은 전면에 열린 테스트 창 안으로만 입력을 보낸다. `usb` 모드는 창에 분리 신호가 표시된 후 실제 케이블을 뽑아야 한다.

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/measure-mouse-motion-preview.py --report build/acceptance-preview-performance.json
.\.venv\Scripts\python.exe tools/verify-mouse-motion-windows.py monitors --confirm-real-input --report build/acceptance-monitors.json
.\.venv\Scripts\python.exe tools/verify-mouse-motion-windows.py focus --port COM5 --confirm-real-input --report build/acceptance-focus.json
.\.venv\Scripts\python.exe tools/verify-mouse-motion-windows.py metrics --port COM5 --confirm-real-input --report build/acceptance-metrics.json
.\.venv\Scripts\python.exe tools/verify-mouse-motion-windows.py usb --port COM5 --confirm-real-input --report build/acceptance-usb.json
```
