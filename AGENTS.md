<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# Python FastAPI Template 작업 지침

## 목적과 구조

Python 3.13에서 FastAPI, Pydantic 2, 동기식 SQLAlchemy 2와 PostgreSQL을 사용하는 JSON:API 1.1 템플릿이다. 선언형 `CrudActions` 기반 Example 자원과 읽기 전용 category·tag, Argon2·JWT 인증, refresh session 회전·정리, Redis 기반 Dramatiq worker를 제공한다. 실행·요청 예제는 `README.md`, 디렉터리별 변경 계약은 아래 하위 지침에서 확인한다.

이메일 계약은 Python 3.13의 Unicode 15.1 기준이다. `.python-version`, `requires-python`, CI와 Docker builder·runtime을 함께 고정한다. Python minor 버전 변경은 NestJS·Rails의 고정 Unicode 프로필과 공통 이메일 회귀 검증을 동반해야 한다.

## 주요 파일

| 파일 | 역할 |
| --- | --- |
| `README.md` | Docker·로컬 실행, 인증·JSON:API 요청, migration, worker와 검증 예제 |
| `pyproject.toml` | 런타임·개발 의존성, Ruff, strict mypy, pytest와 80% coverage 설정 |
| `uv.lock` | uv 의존성 해석 결과; CI와 이미지 빌드는 `uv sync --frozen` 사용 |
| `Dockerfile` | uv builder와 비루트 Python runtime 이미지, ASGI 실행과 readiness healthcheck |
| `docker-compose.yml` | PostgreSQL·Redis·migration·API·worker의 환경과 시작 의존성 |
| `docker-compose.test.yml` | 독립 테스트용 PostgreSQL 서비스와 localhost 포트 매핑 |
| `alembic.ini` | `db/migrations` 경로와 Alembic logging; URL 선택은 migration 환경 모듈에서 처리 |
| `.env.example` | 필수 DB·JWT·Redis 환경 변수, DB 풀·토큰 수명·세션 보존 기간 예시 |
| `.pre-commit-config.yaml` | Ruff 수정·포맷, detect-secrets baseline과 프로젝트 환경의 mypy hook |
| `.secrets.baseline` | detect-secrets 탐지기·필터와 승인된 탐지 결과 기준 |
| `.gitignore` | 가상환경·캐시·로컬 설정·루트 `/docs/`의 미추적 파일 등 Git 제외 범위 |
| `.dockerignore` | 이미지 build context에서 환경·캐시·테스트·설계 문서 제외 |

## 하위 디렉터리

| 경로 | 역할과 하위 지침 |
| --- | --- |
| `.github/` | [GitHub Actions 구성](.github/AGENTS.md) |
| `app/` | [인증·작업·모델·입출력·컨트롤러·프로토콜](app/AGENTS.md) |
| `config/` | [앱 factory, 라우트, DB·인증·broker 설정](config/AGENTS.md) |
| `db/` | [Alembic migration과 명시적 seed](db/AGENTS.md) |
| `docs/` | [인증·worker 설계와 참조 자원 구현 계획](docs/AGENTS.md) |
| `scripts/` | [실제 PostgreSQL을 사용하는 전체 검증 스크립트](scripts/AGENTS.md) |
| `tests/` | [계약별 회귀 테스트와 격리 fixture](tests/AGENTS.md) |

## 아키텍처 규칙

- 모든 컨트롤러는 `JsonApiController`를 상속한다. 리소스 컨트롤러는 `CrudActions`를 통해, 그 밖의 컨트롤러는 직접 상속한다.
- `APIRouter`를 컨트롤러에서 직접 조립하지 않는다. prefix 검증, `Accept` 협상, `Content-Type` 검증은 base가 소유한다.
- `Accept` 협상을 생략하는 컨트롤러는 `negotiate_accept = False`로, 루트에 마운트되는 컨트롤러는 `allow_root_prefix = True`로 그 의도를 코드에 남긴다. router 인자를 빠뜨려서 표현하지 않는다.
- 새 JSON:API 리소스 컨트롤러는 `CrudActions`를 상속한다.
- 컨트롤러에는 모델, 시리얼라이저, create/update/replace 스키마, 관계 스키마, 조회 정책을 선언한다.
- 읽기 전용 자원은 `enable_writes = False`를 선언하며 쓰기 스키마를 생략할 수 있다. 현재 category·tag controller는 관계 없는 GET 목록·단건만 등록한다.
- 공통 액션으로 표현할 수 없는 도메인 동작만 명시적 훅이나 메서드 재정의로 추가한다.
- Rails에 가까운 얇은 컨트롤러 구조를 유지하며 별도 repository 또는 service 계층을 만들지 않는다.
- 라우터는 `config/routes.py`에 명시적으로 등록한다. 자동 탐색이나 숨은 import 등록을 추가하지 않는다.
- 공개 응답 필드와 관계는 시리얼라이저에서만 관리한다. 스키마는 쓰기 입력 검증과 조회 허용 목록을 담당한다.
- `fields[...]` 희소 필드셋은 지원하지 않는다.
- JSON:API 미디어 타입, 문서 모양, 오류 코드와 다국어 응답 계약을 우회하는 일반 JSON 응답을 추가하지 않는다.

## 데이터베이스 규칙

- SQLAlchemy 2의 동기식 `Session`과 PostgreSQL을 사용한다.
- 운영 코드에 SQLite 전용 우회 경로를 추가하지 않는다.
- 모델이나 DB 스키마 변경에는 Alembic 마이그레이션을 반드시 포함한다.
- 시드는 결정적으로 유지하고 서버 시작과 분리한다. 애플리케이션 시작 시 자동 시드하지 않는다.
- `PUT` upsert의 동일 ID 동시 요청 계약과 트랜잭션 롤백 안전성을 약화하지 않는다.
- DB 동작은 실제 PostgreSQL 통합 테스트로 검증한다.

## 검증 명령

```bash
uv sync --frozen
./scripts/check.sh
docker compose config --quiet
docker build --target runtime --tag template-python-fastapi:verify .
docker compose up -d --build --wait
docker compose down -v
```

이 목록은 `README.md`의 `## 검증` 절과 문자열 단위로 동일하게 유지한다. 한쪽만 고치면 `tests/docs/test_readme.py`가 실패한다.

자주 쓰는 개별 단계는 `uv run poe <task>`로도 실행할 수 있다. 태스크 정의는 `pyproject.toml`의 `[tool.poe.tasks]` 한 곳에만 둔다.

| 태스크 | 실행 내용 |
| --- | --- |
| `lint` / `format` / `format-check` | `ruff check .` / `ruff format .` / `ruff format --check .` |
| `typecheck` | `mypy .` (`app`·`config`·`db`·`tests` 전체) |
| `test` | `pytest` (coverage gate 포함) |
| `test-jsonapi` / `test-controllers` / `test-db` | 아래 "변경별 최소 확인"의 좁은 pytest 명령 |
| `migrate` / `seed` | `alembic upgrade head` / `python -m db.seeds` |
| `db-up` / `worker` / `compose-verify` | Compose 서비스 기동과 설정 검증 |
| `check` | `./scripts/check.sh` 호출만 한다. Docker 임시 DB 프로비저닝을 poe로 재구현하지 않는다 |

`check`를 제외한 태스크는 bash 없이 Windows에서도 동작한다. 전체 게이트는 `scripts/check.sh`가 bash 스크립트이므로 Git Bash 또는 WSL이 필요하다.

## 조립점과 변경 순서

- ASGI 진입점은 `config/asgi.py`, 애플리케이션 factory는 `config/main.py:create_app`이다. 새 전역 미들웨어나 예외 처리기는 factory에서 등록 순서까지 검토한다.
- 공개 라우트는 `config/routes.py`에서 controller 인스턴스를 만들고 `api_router`에 명시적으로 포함한다. controller 모듈 import만으로 라우트가 생기게 하지 않는다.
- 자원 추가는 `app/models`의 ORM 모델과 관계를 먼저 정하고, Alembic migration, `app/schemas`의 쓰기·조회 정책, `app/serializers`, 선언형 controller, `config/routes.py`, 관련 테스트 순으로 연결한다.
- `QueryPolicy`에 filter·sort를 추가하거나 `default_sort`·`tie_breaker`를 바꿀 때는 해당 컬럼 조합의 인덱스 필요 여부를 함께 판단하고, 필요하면 같은 변경에서 모델 `__table_args__`와 Alembic migration에 동시에 반영한다. 인덱스를 만들지 않기로 했으면 근거를 정책 선언부에 남긴다.
- `app/models`는 저장 구조와 관계, `app/schemas`는 입력 및 조회 allowlist, `app/serializers`는 공개 JSON:API 표현을 소유한다. 한 계층이 다른 계층의 책임을 대신하지 않는다.
- 공통 CRUD 변경은 `app/controllers/concerns/` 디렉터리, 프로토콜 변경은 `app/jsonapi/`에서만 검토한다. 자원 controller에 공통 동작을 복사하지 않는다.
- concern 안의 검토 지점은 책임별로 나뉜다: 라우트·OpenAPI responses·delegate는 `route_registrar.py`, linkage 해석과 관계 액션은 `relationship_resolver.py`, `PUT` upsert는 `upsert_executor.py`, 요청 문서 파싱·검증은 `document_parsing.py`, 선언 계약과 `before_*`/`after_*` 훅은 `crud_base.py`, 조립과 index/show/create/update/destroy 액션은 `crud_actions.py`다. 공개 진입점은 `CrudActions` 하나이며 상속 체인은 항상 아래 방향으로만 참조한다.

## HTTP·데이터 계약의 경계

- 성공과 오류는 `JsonApiResponse` 및 JSON:API 문서 모델을 통해 반환한다. `application/json` 전용 응답이나 FastAPI 기본 오류 형식을 새 엔드포인트에 섞지 않는다.
- 읽기와 쓰기 모두 `application/vnd.api+json` 협상 규칙을 따른다. 쓰기 본문은 `Content-Type`, 모든 resource route는 `Accept` 검증을 통과해야 한다.
- 조회 허용 범위는 자원별 `QueryPolicy`에 선언한 filter, sort, include만이다. 임의 SQL 열·관계 경로나 `fields[...]` 희소 필드셋을 추가하지 않는다.
- 오류는 `JsonApiException` 또는 등록된 예외 handler로 경로화한다. `Accept-Language`가 `ko` 또는 `en`을 선택하도록 오류 코드와 source 위치를 보존한다.
- 응답의 리소스 type, attributes, relationships와 include 로딩은 serializer 선언이 기준이다. 모델 필드가 존재한다는 이유만으로 외부에 노출하지 않는다.

## PostgreSQL 운영 규칙

- 연결은 `config/database.py`의 동기식 SQLAlchemy 2 `Session`을 사용한다. 비동기 session, SQLite 호환 분기, 별도 repository/service 계층을 도입하지 않는다.
- DB 스키마 수정은 `db/migrations/versions/`의 새 Alembic migration으로만 전달한다. 모델만 바꾸거나 시작 시 DDL을 실행하지 않는다.
- `db/seeds.py`는 고정 식별자와 PostgreSQL upsert로 결정적 데이터를 만든다. 시드 실행은 명시적 명령으로만 수행하고 `create_app`에서 호출하지 않는다.
- `PUT`를 허용한 자원은 PostgreSQL advisory transaction lock과 `INSERT ... ON CONFLICT DO UPDATE`의 원자성 계약을 유지한다. SQLite 대체 구현이나 사전 조회 기반 경쟁 회피를 넣지 않는다.

## 변경별 최소 확인

- 아래의 좁은 `uv run pytest` 명령은 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킨 상태에서 실행한다. URL이 없으면 임의 DB로 대체하지 말고 `./scripts/check.sh`가 만드는 임시 Docker PostgreSQL 전체 게이트를 사용한다.
- JSON:API 문서·협상·오류 변경: `uv run pytest --no-cov tests/jsonapi -q` (`uv run poe test-jsonapi`)
- CRUD·관계·upsert 변경: `uv run pytest --no-cov tests/controllers -q` (`uv run poe test-controllers`)
- migration·seed·세션 변경: `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q` (`uv run poe test-db`)
- 타입 계약 변경: `uv run mypy .` (`uv run poe typecheck`) — `tests/`도 strict 검사 대상이다
- API 변경의 최종 게이트: `uv sync --frozen && ./scripts/check.sh` (`uv run poe check`)
- 컨테이너 설정만 바꿨을 때: `docker compose config --quiet` (`uv run poe compose-verify`)

`./scripts/check.sh`는 별도 `TEST_DATABASE_URL`이 없으면 임시 Docker PostgreSQL을 만들고, Ruff·mypy·pytest·detect-secrets를 순서대로 실행한다. 이 검증을 SQLite 단위 테스트나 개발 서버 기동으로 대체하지 않는다.

## 세부 하위 지침

| 경로 | 소유하는 로컬 계약 |
| --- | --- |
| [config/AGENTS.md](config/AGENTS.md) | factory·명시 route·동기식 engine과 세션 조립 |
| [db/AGENTS.md](db/AGENTS.md) | 호출자 소유 transaction의 결정적 seed |
| [db/migrations/AGENTS.md](db/migrations/AGENTS.md) | Alembic URL 선택과 revision upgrade/downgrade |
| [app/AGENTS.md](app/AGENTS.md) | 모델·schema·serializer·controller의 계층 책임 |
| [tests/AGENTS.md](tests/AGENTS.md) | PostgreSQL fixture와 계약별 회귀 테스트 배치 |

## 인증·작업 큐 조립

- `create_app()`은 인증·DB 설정을 읽고 앱별 engine과 session factory를 `app.state`에 저장한 뒤 예외 handler와 라우터를 등록한다. lifespan 종료 시 engine을 dispose한다. 설정 모듈은 `config/settings.py`의 공통 환경 변수 검증을 사용한다.
- `DATABASE_URL`과 `JWT_SECRET_KEY`는 API 설정에 필수이며 worker broker에는 `REDIS_URL`이 필요하다. `.env.example`은 예시 파일이고 코드의 암묵적인 fallback 설정이 아니다.
- 인증·현재 사용자·Example·category·tag·health 라우트는 `config/routes.py`에서 명시적으로 조립한다. Example 조회는 공개이고 쓰기와 관계 변경에는 활성 사용자의 Bearer access token이 필요하다.
- API import와 시작을 Redis 연결 성공에 결합하지 않는다. Compose에서 API는 migration 완료를, worker는 migration 완료와 Redis 정상 상태를 기다린다.
- worker는 `uv run dramatiq app.jobs`로 별도 실행한다. Example actor의 enqueue 시점은 도메인 호출부가 명시하며 공통 CRUD에서 자동 실행하지 않는다.
- `purge_expired_refresh_sessions`는 보존 기간이 지난 만료 세션을 정리하는 별도 actor다. 배치·잠금·재시도와 수동 enqueue 계약은 [작업 지침](app/jobs/AGENTS.md)을 따른다.
- 인증 변경은 `tests/auth`, `tests/schemas`, `tests/serializers`, `tests/test_auth_controller.py`, `tests/test_user_controller.py`를 함께 검토한다. 작업 큐 변경은 `tests/jobs`, `tests/config/test_broker.py`, Compose 설정을 함께 확인한다. 좁은 pytest 실행에는 위 테스트 DB 전제조건을 적용한다.

## 의존성과 문서 관리

- 내부 연결: `config`가 `app`을 조립하고, `db/migrations`가 ORM metadata를 사용하며, `tests`는 실제 factory·migration·PostgreSQL fixture로 동작을 검증한다.
- 외부 런타임: FastAPI·Uvicorn, Pydantic·email-validator, SQLAlchemy·psycopg·Alembic, PyJWT·pwdlib Argon2, Dramatiq·Redis.
- 개발 도구: uv·poethepoet, pytest·pytest-cov·HTTPX, Ruff, strict mypy, PyYAML·types-pyyaml, pre-commit·detect-secrets, Docker Compose. 의존성 변경 시 manifest와 lock을 함께 검토한다.
- `AGENTS.md`는 UTF-8로 저장한다. 하위 문서는 바로 위 디렉터리의 `AGENTS.md`를 Parent 태그로 연결하고, 실제 파일과 테스트에서 확인한 계약만 설명한다.
- 루트 `/docs/` ignore 패턴은 새 미추적 파일에 적용된다. 이 저장소의 설계·계획 문서와 `AGENTS.md`는 명시적으로 Git 추적에 포함한다. `tests/docs/`는 해당 루트 패턴의 대상이 아니며 문서·도구 설정 회귀 테스트를 보관한다.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
