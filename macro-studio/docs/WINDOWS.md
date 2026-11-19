# Macro Studio v1.4.0 for Windows x64

ZIP을 푼 뒤 `MacroStudio/MacroStudio.exe`를 실행합니다. 실행 파일과 `_internal` 폴더를 함께 유지하세요. 앱에 Python, Qt, Blockly가 포함되어 있습니다.

1. **파일 → 열기…**에서 `examples/mixed-loop.macroproj`를 선택합니다.
2. **파일 → 다른 이름으로 저장…**에서 쓰기 가능한 폴더에 복사본을 저장합니다.
3. **시작**을 눌러 드라이런을 실행하고 **보내기**로 예제의 `Ctrl+F7`을 모의합니다.
4. 실행 로그에 누름과 해제가 기록되는지 확인하고 **정지**를 누릅니다.

일반 편집과 드라이런은 오프라인에서 사용할 수 있습니다. OCR 블록을 사용하는 경우 Tesseract를 별도로 설치하고 `tesseract.exe`가 있는 폴더를 Windows PATH에 추가하세요.

마우스 모션 기능은 패키지의 `docs/MOUSE_MOTION_PROFILE.md`와 `examples/mouse_motion_showcase.macroproj`에서 확인할 수 있습니다.

**이미지 인식** 메뉴에서 화면 템플릿, 인식 프로파일, 미리보기를 관리할 수 있습니다. 배포본에는 MSS와 OpenCV가 포함됩니다. [시각 인식 안내](docs/VISUAL_RECOGNITION.md)

Python 편집은 `examples/python-showcase.macroproj`를 열고 `검증` 후 시작하면 확인할 수 있습니다. Python 메뉴에서 외부 LLM 작성 자료와 예제 8개를 ZIP으로 내보낼 수 있습니다. [Python 사용자 가이드](docs/PYTHON_USER_GUIDE.md)와 [인수 검증 결과](docs/PYTHON_ACCEPTANCE.md)를 참고하세요.
