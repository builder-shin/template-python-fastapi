<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 설정과 실행 조립 테스트

## 목적과 주요 파일

환경 설정의 유효성, factory·worker import 순서, Compose 서비스 의존성과 Alembic URL 선택을 검증한다. 설정만 검사하는 사례와 실제 migration을 실행하는 사례를 구별한다.

| 파일 | 역할 |
| --- | --- |
| `test_auth.py` | 32 UTF-8 바이트 JWT secret, 환경 기본값·경계, JWT와 독립된 refresh 보존 기간, factory 선검증 |
| `test_database.py` | 필수 DB URL·pool 옵션, import 부작용 부재, worker factory의 동시 초기화, 앱별 세션·lifespan 종료 |
| `test_broker.py` | 필수 Redis URL, actor import 이전 broker 설정, API factory의 broker import 금지 |
| `test_compose.py` | 서비스 topology·내부 Redis URL·JWT readiness, worker 전용 refresh 보존 기간 |
| `test_migrations.py` | Alembic의 TEST_DATABASE_URL → DATABASE_URL → 명시 config URL 우선순위, head·check와 URL 누락 실패 |
| `test_routes.py` | 명시적 controller 목록·prefix, serializer resource path·관계 alias·type, 읽기 전용 참조 자원 |
| `test_settings.py` | require_env의 누락·공백 거부, read_int 기본값·override와 변수명을 포함한 오류 |
| `__init__.py` | 설정 테스트 패키지 경계 |

## 작업 규칙과 공통 패턴

- 환경 변수는 `monkeypatch`로 설정·삭제한다. secret 길이는 문자 수가 아닌 UTF-8 byte 수로 검증하고, factory가 router 포함 전에 설정을 저장하는 순서를 보존한다.
- `DATABASE_URL`과 `REDIS_URL` 누락은 명시적으로 실패해야 한다. 환경 helper가 빈 문자열·공백을 거부하고 숫자 오류에 해당 변수명을 남기는지 확인한다.
- DB 모듈 import만으로 engine이나 `SessionFactory` 전역 객체가 생기지 않아야 한다. worker의 lazy `get_session_factory()`는 재호출과 여러 thread의 동시 최초 호출에서 한 번만 초기화되는지 확인한다.
- 앱별 engine·session factory와 lifespan dispose를 검증한다. CRUD dependency는 세션을 열고 닫지만 auth dependency는 아직 열지 않은 factory를 반환한다. auth factory를 generator로 바꾸거나 `request.state.session`에 결합하지 않는다. 실제 ORM 저장·migration 동작은 PostgreSQL fixture에서 확인한다.
- broker·actor의 import 순서는 별도 Python subprocess에서 검사하여 기존 module cache의 영향을 차단한다. API factory는 Redis broker 설정 import에 의존하지 않아야 한다.
- refresh 보존 기간은 JWT 설정 없이 읽을 수 있고 기본 604800초, 0 허용·음수 거부를 유지한다. Compose에서는 worker에만 전달하며 API 설정과 섞지 않는다.
- Compose 테스트는 `docker compose config --format json`의 해석된 결과를 사용한다. API는 migration 완료만, worker는 migration 완료와 Redis 정상 상태를 기다리는 계약을 함께 확인한다.
- Alembic head revision·테이블 기대값은 새 migration과 함께 검토한다. `test_migrations.py`는 테스트 DB를 `base`로 내렸다가 올리므로 다른 실행과 DB를 공유하지 않는다.
- route 목록은 Examples·ExampleCategories·ExampleTags controller의 명시 등록을 기준으로 검사한다. prefix와 serializer 경로, lowerCamelCase type, 쓰기 관계 alias의 공개 관계 포함 여부를 확인하고 참조 자원은 GET만 허용한다.

## 검증

좁은 실행은 독립된 `*_test` PostgreSQL의 `TEST_DATABASE_URL`을 준비한 뒤 `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q`를 사용한다. URL이 없으면 `./scripts/check.sh`가 임시 Docker PostgreSQL을 준비하는 전체 게이트를 실행한다. Compose 설정 검사에는 Docker Compose CLI가 필요하다.

## 의존성

- 내부: `config/settings.py`, `config/auth.py`, `config/database.py`, `config/broker.py`, `config/main.py`, `config/routes.py`, controller·serializer 선언, `app/jobs`, `db/migrations`, `Dockerfile`, `docker-compose.yml`, `.env.example`.
- 외부: pytest, FastAPI·Starlette, SQLAlchemy·psycopg, Alembic CLI, Dramatiq와 Docker Compose CLI.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
