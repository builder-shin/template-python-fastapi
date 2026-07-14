# Python FastAPI Template 작업 지침

## 아키텍처 규칙

- 새 JSON:API 리소스 컨트롤러는 `CrudActions`를 상속한다.
- 컨트롤러에는 모델, 시리얼라이저, create/update/replace 스키마, 관계 스키마, 조회 정책을 선언한다.
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
uv sync
./scripts/check.sh
docker compose config --quiet
docker compose up -d --build --wait
docker compose down -v
```

## 조립점과 변경 순서

- ASGI 진입점은 `config/asgi.py`, 애플리케이션 factory는 `config/main.py:create_app`이다. 새 전역 미들웨어나 예외 처리기는 factory에서 등록 순서까지 검토한다.
- 공개 라우트는 `config/routes.py`에서 controller 인스턴스를 만들고 `api_router`에 명시적으로 포함한다. controller 모듈 import만으로 라우트가 생기게 하지 않는다.
- 자원 추가는 `app/models`의 ORM 모델과 관계를 먼저 정하고, Alembic migration, `app/schemas`의 쓰기·조회 정책, `app/serializers`, 선언형 controller, `config/routes.py`, 관련 테스트 순으로 연결한다.
- `app/models`는 저장 구조와 관계, `app/schemas`는 입력 및 조회 allowlist, `app/serializers`는 공개 JSON:API 표현을 소유한다. 한 계층이 다른 계층의 책임을 대신하지 않는다.
- 공통 CRUD 변경은 `app/controllers/concerns/crud_actions.py`, 프로토콜 변경은 `app/jsonapi/`에서만 검토한다. 자원 controller에 공통 동작을 복사하지 않는다.

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
- JSON:API 문서·협상·오류 변경: `uv run pytest --no-cov tests/jsonapi -q`
- CRUD·관계·upsert 변경: `uv run pytest --no-cov tests/controllers -q`
- migration·seed·세션 변경: `uv run pytest --no-cov tests/config tests/integration tests/test_database_fixtures.py -q`
- API 변경의 최종 게이트: `uv sync && ./scripts/check.sh`
- 컨테이너 설정만 바꿨을 때: `docker compose config --quiet`

`./scripts/check.sh`는 별도 `TEST_DATABASE_URL`이 없으면 임시 Docker PostgreSQL을 만들고, Ruff·mypy·pytest·detect-secrets를 순서대로 실행한다. 이 검증을 SQLite 단위 테스트나 개발 서버 기동으로 대체하지 않는다.

## 세부 하위 지침

| 경로 | 소유하는 로컬 계약 |
| --- | --- |
| `config/AGENTS.md` | factory·명시 route·동기식 engine과 세션 조립 |
| `db/AGENTS.md` | 호출자 소유 transaction의 결정적 seed |
| `db/migrations/AGENTS.md` | Alembic URL 선택과 revision upgrade/downgrade |
| `app/AGENTS.md` | 모델·schema·serializer·controller의 계층 책임 |
| `tests/AGENTS.md` | PostgreSQL fixture와 계약별 회귀 테스트 배치 |
