<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 검증 스크립트

## 목적과 주요 파일

| 파일 | 역할 |
| --- | --- |
| `check.sh` | 격리된 테스트 PostgreSQL 준비, 정적 검사·전체 pytest·비밀 탐지와 종료 시 정리 |

## 작업 지침과 공통 패턴

- `check.sh`는 Bash의 `set -euo pipefail`을 사용하고 호출한 위치와 관계없이 저장소 루트로 이동한다. Windows에서도 Bash와 사용 가능한 uv·Docker CLI가 필요하다.
- `TEST_DATABASE_URL`이 없으면 실행별 PID·난수 Compose project와 `TEST_DB_PORT=0`으로 DB를 시작하고, 실제 배정 포트에서 `_test` URL을 만든다. 고정 project나 고정 포트로 바꾸지 않는다.
- 사용자가 제공한 URL은 그대로 사용한다. 이 경우에도 대상이 독립된 `*_test` PostgreSQL인지 확인해야 하며, 개발 DB를 대체값으로 주지 않는다.
- `COVERAGE_FILE`이 없으면 실행별 파일을 만들고 `EXIT` trap에서 제거한다. 외부에서 제공한 coverage 파일은 삭제하지 않는다.
- cleanup은 이 실행이 시작한 Compose project에만 `down -v`를 적용한다. 기동 실패 시에도 정리하도록 시작 플래그를 DB 실행 전에 설정한다.
- 검사 순서는 Ruff lint → Ruff format check → mypy → pytest → pre-commit detect-secrets다. 오류를 무시하거나 coverage·PostgreSQL 검증을 생략하지 않는다.
- strict mypy는 `tests/`까지 포함한다. `uv run poe check`는 이 스크립트를 호출하며, 개별 poe 태스크의 정의는 루트 `pyproject.toml`에만 둔다.

## 검증

- 스크립트 수정 시 `tests/scripts/test_check_script.py`에서 임의 작업 디렉터리 호출, project·port·coverage 격리를 함께 확인한다. 이 테스트의 가짜 CLI는 스크립트 조립 검증용이다.
- 좁은 실행은 독립 `TEST_DATABASE_URL`을 설정한 후 `uv run pytest --no-cov tests/scripts -q`를 사용한다. URL이 없으면 `./scripts/check.sh` 전체 게이트를 실행한다.
- 실제 실행 경로의 최종 검증은 `uv sync --frozen` 후 `./scripts/check.sh`다.

## 의존성

- 내부: `docker-compose.test.yml`, `pyproject.toml`, `.pre-commit-config.yaml`, `.secrets.baseline`, [스크립트 테스트](../tests/scripts/AGENTS.md).
- 외부: Bash, uv, Docker Compose, PostgreSQL과 프로젝트 개발 도구.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
