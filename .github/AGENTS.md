<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# GitHub 자동 검증

## 목적

push와 pull request에서 실행되는 저장소 CI 구성을 모은다. 현재 직접 파일은 없으며 실행 정의는 `workflows/`에 있다.

## 하위 디렉터리

| 디렉터리 | 역할 |
| --- | --- |
| [workflows/](workflows/AGENTS.md) | 의존성 고정 설치, PostgreSQL 검사, Compose와 runtime 이미지 검증 |

## 작업·검증 지침

- 실제 workflow 변경은 하위 지침을 따른다. 검증 명령의 기준은 루트 `scripts/check.sh`와 `README.md`다.
- 문서만 바꾸면 파일·상위 참조·하위 링크를 확인하고 `git diff --check`를 실행한다.

## 의존성

- 내부: `scripts/check.sh`, `pyproject.toml`, `uv.lock`, Docker 설정.
- 외부: GitHub Actions; workflow는 Ubuntu runner에서 실행한다.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
