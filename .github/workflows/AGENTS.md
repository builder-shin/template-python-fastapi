<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# CI workflow

## 목적과 주요 파일

| 파일 | 역할 |
| --- | --- |
| `ci.yml` | push·pull_request에서 Ubuntu `checks` job을 실행하는 CI |

## 작업 지침과 공통 패턴

- 현재 권한은 `contents: read`다. checkout 후 `astral-sh/setup-uv`로 uv `0.9.30`과 cache를 설정한다.
- 실행 순서는 `uv sync --frozen` → `./scripts/check.sh` → `docker compose config --quiet` → `docker build --target runtime --tag template-python-fastapi:ci .`다.
- DB 준비와 cleanup은 `check.sh`에 맡긴다. workflow에 SQLite 대체 경로나 다른 검사 구현을 복사하지 않는다.
- uv 버전·lock 설치 방식·runtime target을 변경할 때 `Dockerfile`과 README 검증 예제도 비교한다.
- 현재 workflow는 이미지 빌드까지 수행한다. 실제 Compose 전체 기동 검증은 루트 지침의 별도 명령이다.

## 검증

- 변경 시 위 CI 명령을 저장소 루트에서 확인한다. Compose 파일 변경은 `docker compose config --quiet`도 필요하다.
- Git 추적 중인 `tests/docs/test_readme.py`는 trigger, 권한, job, 명령 순서와 루트 지침의 검증 명령 일치를 검사한다. `test_tooling.py`는 strict mypy·poe·pre-commit 구성을, `test_agents_guides.py`는 계층 지침의 실제 symbol·파일 수·직렬화 계약을 확인한다.
- 좁은 pytest에는 독립 `TEST_DATABASE_URL`을 먼저 설정한다. URL이 없으면 `./scripts/check.sh`로 임시 PostgreSQL 전체 게이트를 사용한다.

## 의존성

- 내부: [검증 스크립트](../../scripts/AGENTS.md), `pyproject.toml`, `uv.lock`, Dockerfile·Compose, `README.md`.
- 외부: GitHub Actions, `actions/checkout@v4`, `astral-sh/setup-uv@v7`, Ubuntu, uv, Docker.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
