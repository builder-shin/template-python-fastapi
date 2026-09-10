<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# Controller 계층 지침

## Purpose

공개 HTTP 액션과 요청 transaction을 조립한다. 자원 CRUD는 공통 concern과 선언형 controller로 연결하고, 기존 인증·현재 사용자·health 경로는 명시 액션으로 제공한다. 애플리케이션 전체 route 등록은 `config/routes.py`가 소유한다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | controller 패키지 표시 |
| `health_controller.py` | `/health/live` liveness와 `/health/ready` PostgreSQL readiness |

## Subdirectories

| 경로 | 책임 |
| --- | --- |
| [api/](api/AGENTS.md) | 버전별 API controller |
| [concerns/](concerns/AGENTS.md) | 공통 CRUD·관계 액션, JSON:API route와 입력 문서 생성 |

## For AI Agents

### Working In This Directory

- 모든 controller는 `JsonApiController`를 상속하고 받은 `self.router`에 route를 등록한다. 자원은 `CrudActions`를 통해 상속하며 controller에서 `APIRouter(...)`를 다시 조립하지 않는다. 도메인 선언과 명시 hook만 controller에 두고 repository/service 계층이나 공통 CRUD 복사를 추가하지 않는다.
- controller 인스턴스는 `config/routes.py`에서 만들고 router를 명시적으로 포함한다. decorator·자동 탐색·숨은 import 등록을 추가하지 않는다.
- base가 `validate_route_prefix`, Accept 의존성, JsonApiRoute와 self.prefix를 소유한다. 공용 오류 응답은 `jsonapi_error_responses()`를 사용하고 operationId의 근거인 route name을 명시한다.
- 자원 route는 `JsonApiRoute`, JSON:API Accept 의존성, `JsonApiResponse`·문서 모델·안정된 오류를 유지한다. body가 있는 route의 Content-Type은 FastAPI body 검증 전에 검사한다.
- HealthController는 `negotiate_accept=False`, `allow_root_prefix=True`로 두 GET probe의 Accept 생략·루트 마운트를 명시한다. 이 경우에도 JsonApiRoute는 유지하고 JSON:API 문서를 반환한다. `live()`는 DB session을 해석하지 않고, `ready()`는 `SELECT 1` 실패를 안전한 503 `INTERNAL_SERVER_ERROR`로 바꾼다. 이 계약을 일반 자원 route에 확대하지 않는다.
- 빈 204 응답은 기존 destroy·관계 mutation·logout의 계약이다. JSON:API 문서가 필요한 성공·오류를 일반 JSON 응답으로 바꾸지 않는다.

### Testing Requirements

`TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/controllers tests/test_example_controller.py tests/test_auth_controller.py tests/test_user_controller.py tests/test_health_controller.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 게이트를 사용한다. factory 기반 공개 route·OpenAPI와 실제 DB rollback·경쟁 동작을 함께 확인한다.

### Common Patterns

참조 자원은 `enable_writes=False`로 GET만 등록하고 write schema를 생략한다. 읽기·쓰기 의존성은 `read_dependencies`·`write_dependencies`에 선언한다. 응답 필드·include는 serializer, 입력·조회 범위는 schema에 맡기고 controller는 transaction과 HTTP 의미를 연결한다.

## Dependencies

- 내부: [config](../../config/AGENTS.md)의 명시 route·Session, [schemas](../schemas/AGENTS.md), [serializers](../serializers/AGENTS.md), [JSON:API](../jsonapi/AGENTS.md), [auth](../auth/AGENTS.md).
- 외부: FastAPI/Starlette, SQLAlchemy 2, Pydantic.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
