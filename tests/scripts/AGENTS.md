<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 검증 스크립트 회귀 테스트

## 목적과 주요 파일

독립 저장소에서 `scripts/check.sh`가 테스트용 Compose project·포트·coverage 출력을 격리하고 정리하는 계약을 검증한다.

| 파일 | 역할 |
| --- | --- |
| `test_check_script.py` | pre-commit 기준 경로, 가짜 docker·uv를 통한 Compose project 격리·동적 포트 URL·coverage 위치·정리 명령 |

## 작업 규칙과 공통 패턴

- `tmp_path/bin`에 실행 가능한 Bash stub을 만들고 해당 테스트 subprocess의 PATH 앞에 붙인다. 사용자 설치나 저장소 실행 파일을 교체하지 않는다.
- stub의 command log로 up/down의 동일 project 이름, host port `0`, 발견된 포트를 사용한 `TEST_DATABASE_URL`, project별 coverage 파일을 확인한다.
- 테스트 subprocess는 임시 작업 디렉터리에서 실행한다. 스크립트가 자체 저장소 루트를 찾는 계약을 유지한다.
- 이 회귀는 Docker·uv 명령 조립을 검증한다. 실제 PostgreSQL·Ruff·mypy·pytest·detect-secrets 실행 성공은 전체 `./scripts/check.sh` 게이트에서 확인한다.
- poe task·strict mypy·pre-commit 설정의 정적 계약은 `tests/docs/test_tooling.py`가 담당한다. `uv run poe check`도 전체 게이트의 단일 진입점인 `scripts/check.sh`를 호출해야 한다.
- shebang·실행 비트와 직접 script 실행을 사용하는 테스트이므로 Bash/POSIX 실행 환경을 고려한다. Windows에서 통과시키기 위한 운영 SQLite 우회 경로를 만들지 않는다.

## 검증

상위 실행 규칙에 따라 독립된 `*_test` PostgreSQL의 `TEST_DATABASE_URL`이 설정된 상태에서 `uv run pytest --no-cov tests/scripts -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다. stub 회귀 내부에서는 외부 URL을 제거해 스크립트의 DB 준비 분기를 검사한다.

## 의존성

- 내부: `scripts/check.sh`, `.pre-commit-config.yaml`, `.secrets.baseline` 경로 계약.
- 외부: pytest, Python subprocess·pathlib, Bash/POSIX executable 지원. stub 검증만으로 실제 Docker 실행을 대체하지 않는다.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
