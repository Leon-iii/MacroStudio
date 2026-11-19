# 버전과 확장 범위

| 항목 | 의미 |
|---|---|
| 제품 v1.3.0 | 이전 배포 버전이다. [시각 인식 명세](../../MacroStudio_Visual_Recognition_Spec_v1_3.md)의 기능을 포함한다. |
| 제품 v1.4.0 | 현재 릴리스다. 제한 Python DSL, 공통 IR 변환, Python 편집기와 외부 LLM 작성 자료·선언형 서비스 팩을 제공한다. [인수 결과](PYTHON_ACCEPTANCE.md)와 [지원 범위](PYTHON_ROUNDTRIP_CONTRACT.md)를 따른다. |
| Auto Maple Rune Recognition Extension | 과거 파일명은 `MacroStudio_Rune_Recognition_Spec_v1_4.md`다. 파일명의 v1.4는 이 확장의 과거 임시 명명이다. 해당 문서는 `adca3b3`의 Git 이력으로 확인할 수 있다. |
| 프로젝트 스키마 | 배포 v1.3은 1을 사용한다. v1.4는 1과 2를 읽으며, Python 편집 자료가 생긴 파일에 2를 적용한다. 기존 블록 파일은 1을 유지한다. |
| 실행 IR | 현재 1~4를 읽는다. Python 컴파일러는 지역 변수가 필요한 워크플로에 IR 4를 쓴다. 다른 명령은 기존 의미를 표현할 수 있는 가장 낮은 버전을 사용한다. |

선택적 `auto-maple` 서브모듈은 로컬 커밋 `3ecc9f6`을 가리킨다. 등록 시 이 커밋은 `origin/main`보다 1개 앞서 있었으며 원격 게시가 필요한 상태다. Macro Studio의 실행·테스트·Windows 패키지는 이 서브모듈을 사용하지 않는다.
