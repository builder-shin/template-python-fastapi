# Python FastAPI Template

FastAPI, SQLAlchemy 2, PostgreSQL, Alembic으로 구성한 JSON:API 1.1 템플릿입니다. 자체 JWT 인증과 Redis 기반 Dramatiq worker를 제공하며, Rails 컨트롤러의 공통 CRUD 액션과 비슷하게 `CrudActions`를 상속합니다. 도메인 컨트롤러에는 모델·스키마·시리얼라이저·조회 정책만 선언합니다.

## 구조

```text
app/controllers/concerns/                      # Rails형 공통 CRUD·관계 액션(`CrudActions`)
app/controllers/api/v1/examples_controller.py # 예시 리소스 선언
app/auth/                                      # 비밀번호·JWT·refresh session
app/jobs/                                      # Dramatiq actor
app/models/                                    # SQLAlchemy 모델
app/schemas/                                   # 쓰기 입력과 조회 허용 목록
app/serializers/                               # 공개 응답 필드와 관계
app/jsonapi/                                   # 문서, 오류, 협상, 조회, 응답
config/routes.py                               # 명시적 라우터 등록
config/main.py                                 # FastAPI 앱 팩토리
db/migrations/                                 # Alembic 마이그레이션
db/seeds.py                                    # 결정적인 예시 시드
tests/                                         # 단위·PostgreSQL 통합 테스트
```

공개 응답 필드는 시리얼라이저의 `attributes`와 `relationships`에서만 관리합니다. `fields[...]` 희소 필드셋은 지원하지 않습니다.

## Docker로 실행

DB가 정상 상태가 되면 마이그레이션이 실행되고, 성공한 뒤 API와 worker가 시작됩니다. API는 Redis 장애와 관계없이 시작하고, worker는 Redis가 정상 상태가 된 뒤 시작합니다.

```bash
docker compose up -d --build --wait
docker compose exec api python -m db.seeds
```

- API: `http://localhost:4000`
- OpenAPI 문서: `http://localhost:4000/api-docs`
- 상태 확인: `http://localhost:4000/health/live`, `http://localhost:4000/health/ready`

Redis와 worker 상태는 다음 명령으로 확인합니다.

```bash
docker compose ps redis worker
docker compose logs worker
```

시드는 서버 시작 시 자동 실행되지 않습니다. 필요한 환경에서만 위 명령으로 명시적으로 실행합니다.

```bash
docker compose down -v
```

## 로컬 프로세스로 실행

PostgreSQL만 Docker로 실행하고 API는 로컬에서 실행할 수 있습니다.

```bash
docker compose up -d --wait db
uv sync
export DATABASE_URL='postgresql+psycopg://fastapi:fastapi@localhost:5432/fastapi_template' # pragma: allowlist secret
export JWT_SECRET_KEY='development-only-jwt-secret-key-at-least-32-bytes' # pragma: allowlist secret
uv run alembic upgrade head
uv run python -m db.seeds
uv run uvicorn config.asgi:application --reload --port 4000
```

`DATABASE_URL`과 `JWT_SECRET_KEY`에는 애플리케이션 코드상의 기본값이 없으므로 Compose 밖에서 실행할 때는 반드시 셸 환경 변수로 내보내야 합니다. 위 두 값은 `.env.example`의 개발 전용 값과 같습니다. 풀 설정(`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`)만 선택 값이며 기본값도 `.env.example`과 같습니다. 전체 목록은 아래 '필수 환경 변수'를 참고합니다.

## 필수 환경 변수

`DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`에는 애플리케이션 코드상의 암묵적인 기본값이 없습니다. Compose와 `.env.example`이 개발 전용 값을 제공하며, 그 밖의 환경에서 값을 빠뜨리면 `DATABASE_URL is required`처럼 변수 이름이 담긴 오류와 함께 프로세스가 시작되지 않습니다. `DB_POOL_SIZE`처럼 정수를 기대하는 변수에 잘못된 값을 주면 `DB_POOL_SIZE must be an integer`로 실패합니다.

| 변수 | 필요한 프로세스 | 비고 |
| --- | --- | --- |
| `DATABASE_URL` | API, worker, 마이그레이션, 시드 | PostgreSQL 전용입니다. |
| `REDIS_URL` | worker | API 프로세스는 broker를 import하지 않습니다. |
| `JWT_SECRET_KEY` | API | UTF-8 기준 최소 32바이트입니다. |

`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `JWT_*` 만료 설정과 `REFRESH_SESSION_RETENTION_SECONDS`는 선택 값이며 기본값은 `.env.example`과 같습니다.

## JWT 설정

Compose의 `JWT_SECRET_KEY` 기본값과 `.env.example` 값은 로컬 개발 전용입니다. 운영 환경에서는 예측할 수 없는 별도 비밀키를 사용해야 하며 UTF-8 기준 최소 32바이트여야 합니다. Compose 밖에서 실행할 때는 `JWT_SECRET_KEY`에 암묵적인 기본값이 없으므로, 누락하거나 32바이트보다 짧으면 애플리케이션이 시작되지 않습니다.

```bash
export JWT_SECRET_KEY="$(openssl rand -hex 32)" # pragma: allowlist secret
```

기본 access token 만료 시간은 900초(15분), refresh token 만료 시간은 2,592,000초(30일)입니다. issuer와 audience 기본값은 모두 `template-python-fastapi`입니다.

## 인증 API

회원가입 요청은 `users` 리소스를 사용합니다.

```bash
read -r -s -p '비밀번호(12~128자): ' AUTH_PASSWORD
printf '\n'

jsonapi_credentials_document() {
  uv run python -c '
import json
import sys

parts = sys.stdin.buffer.read().split(b"\0")
if len(parts) != 4 or parts[-1]:
    raise SystemExit("invalid credentials input")
resource_type, email, password = (part.decode("utf-8") for part in parts[:3])
document = {
    "data": {
        "type": resource_type,
        "attributes": {"email": email, "password": password},
    }
}
sys.stdout.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
'
}

jsonapi_refresh_document() {
  uv run python -c '
import json
import sys

parts = sys.stdin.buffer.read().split(b"\0")
if len(parts) != 2 or parts[-1]:
    raise SystemExit("invalid refresh token input")
document = {
    "data": {
        "type": "refreshTokens",
        "attributes": {"refreshToken": parts[0].decode("utf-8")},
    }
}
sys.stdout.write(json.dumps(document, separators=(",", ":")))
'
}

printf '%s\0%s\0%s\0' 'users' 'person@example.com' "${AUTH_PASSWORD}" |
  jsonapi_credentials_document |
  curl \
    --request POST \
    --header 'Accept: application/vnd.api+json' \
    --header 'Content-Type: application/vnd.api+json' \
    --data-binary @- \
    http://localhost:4000/api/v1/auth/register
```

로그인 응답의 `accessToken`과 `refreshToken`은 JSON body의 `authTokens` 리소스에 포함됩니다. 다음 helper와 command substitution은 token 응답을 stdout이나 파일에 남기지 않고 현재 shell 변수에 저장합니다.

```bash
jsonapi_attribute() {
  uv run python -c \
    'import json, sys; print(json.load(sys.stdin)["data"]["attributes"][sys.argv[1]])' \
    "$1"
}

AUTH_RESPONSE="$(
  printf '%s\0%s\0%s\0' 'authCredentials' 'person@example.com' "${AUTH_PASSWORD}" |
    jsonapi_credentials_document |
    curl \
      --silent \
      --show-error \
      --fail-with-body \
      --request POST \
      --header 'Accept: application/vnd.api+json' \
      --header 'Content-Type: application/vnd.api+json' \
      --data-binary @- \
      http://localhost:4000/api/v1/auth/login
)"

ACCESS_TOKEN="$(printf '%s' "${AUTH_RESPONSE}" | jsonapi_attribute accessToken)"
REFRESH_TOKEN="$(printf '%s' "${AUTH_RESPONSE}" | jsonapi_attribute refreshToken)"
```

refresh token을 회전하면 기존 refresh token은 즉시 폐기됩니다.

```bash
REFRESH_RESPONSE="$(
  printf '%s\0' "${REFRESH_TOKEN}" |
    jsonapi_refresh_document |
    curl \
      --silent \
      --show-error \
      --fail-with-body \
      --request POST \
      --header 'Accept: application/vnd.api+json' \
      --header 'Content-Type: application/vnd.api+json' \
      --data-binary @- \
      http://localhost:4000/api/v1/auth/refresh
)"

ACCESS_TOKEN="$(printf '%s' "${REFRESH_RESPONSE}" | jsonapi_attribute accessToken)"
REFRESH_TOKEN="$(printf '%s' "${REFRESH_RESPONSE}" | jsonapi_attribute refreshToken)"
```

로그아웃은 제시한 refresh session을 폐기하고 본문 없이 `204`를 반환합니다.

```bash
printf '%s\0' "${REFRESH_TOKEN}" |
  jsonapi_refresh_document |
  curl \
    --request POST \
    --header 'Accept: application/vnd.api+json' \
    --header 'Content-Type: application/vnd.api+json' \
    --data-binary @- \
    http://localhost:4000/api/v1/auth/logout
```

현재 사용자는 access token을 Bearer 인증 헤더로 조회합니다.

```bash
curl \
  --header 'Accept: application/vnd.api+json' \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  http://localhost:4000/api/v1/users/me
```

refresh token은 클라이언트의 보안 저장소에 안전하게 보관해야 합니다. 서버는 token을 cookie에 저장하지 않고 JSON body로만 발급합니다. 위 shell 변수는 예제 요청을 마친 뒤 `unset`하거나 shell을 종료합니다. `logout`은 refresh session만 폐기하므로 이미 발급된 access token은 만료될 때까지 최대 15분 동안 유효할 수 있습니다.

## 데이터베이스 마이그레이션

모델이나 스키마를 변경할 때는 반드시 마이그레이션을 함께 추가하고 업그레이드와 다운그레이드를 확인합니다.

```bash
uv run alembic current
uv run alembic revision --autogenerate -m "변경 내용"
uv run alembic upgrade head
uv run alembic downgrade -1
```

## JSON:API 요청

클라이언트는 응답 형식을 명확히 하기 위해 `Accept: application/vnd.api+json`을 지정합니다. 호환되지 않는 `Accept`는 `406`으로 거부됩니다. 본문이 있는 요청에는 `Content-Type: application/vnd.api+json`을 반드시 지정합니다.

```bash
curl \
  --globoff \
  --header 'Accept: application/vnd.api+json' \
  'http://localhost:4000/api/v1/examples?include=category,tags&sort=-score&page[number]=1&page[size]=20'
```

```bash
curl \
  --request POST \
  --header 'Accept: application/vnd.api+json' \
  --header 'Content-Type: application/vnd.api+json' \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  --data '{"data":{"type":"examples","attributes":{"title":"예시","status":"active","score":80}}}' \
  http://localhost:4000/api/v1/examples

unset ACCESS_TOKEN REFRESH_TOKEN AUTH_PASSWORD AUTH_RESPONSE REFRESH_RESPONSE
```

Example 읽기 요청은 공개되어 있고 Example 쓰기 요청과 관계 변경은 활성 사용자의 Bearer access token이 필요합니다.

## 예시 리소스 액션

| 메서드 | 경로 | 동작 |
| --- | --- | --- |
| `GET` | `/api/v1/examples` | 목록 |
| `POST` | `/api/v1/examples` | 생성 |
| `GET` | `/api/v1/examples/{id}` | 단건 조회 |
| `PATCH` | `/api/v1/examples/{id}` | 일부 수정 |
| `PUT` | `/api/v1/examples/{id}` | 전체 교체 또는 같은 ID로 upsert |
| `DELETE` | `/api/v1/examples/{id}` | 삭제 |
| `GET` | `/api/v1/examples/{id}/relationships/category` | category linkage 조회 |
| `PATCH` | `/api/v1/examples/{id}/relationships/category` | category linkage 교체 |
| `GET` | `/api/v1/examples/{id}/category` | 연결된 category 리소스 조회 |
| `GET` | `/api/v1/examples/{id}/relationships/tags` | tags linkage 조회 |
| `POST` | `/api/v1/examples/{id}/relationships/tags` | tags linkage 추가 |
| `PATCH` | `/api/v1/examples/{id}/relationships/tags` | tags linkage 전체 교체 |
| `DELETE` | `/api/v1/examples/{id}/relationships/tags` | tags linkage 제거 |
| `GET` | `/api/v1/examples/{id}/tags` | 연결된 tag 리소스 조회 |

## 참조 자원

분류와 라벨은 읽기 전용 컬렉션으로도 조회할 수 있습니다. 관계 선택기처럼
고를 목록이 필요한 화면을 위한 것이며, 쓰기 라우트는 없습니다.

```text
GET /api/v1/categories       filter[name] · sort=name,createdAt · page[...]
GET /api/v1/categories/{id}
GET /api/v1/tags
GET /api/v1/tags/{id}
```

기본 정렬은 `name` 오름차순입니다. JSON:API 자원 타입은 각각
`exampleCategories`와 `exampleTags`로, URL 경로와 다릅니다.

```bash
curl --globoff -fsS \
  -H 'Accept: application/vnd.api+json' \
  'http://localhost:4000/api/v1/categories?filter[name][contains]=문서'
```

## 조회 파라미터

- 필터: `filter[status]=active`, `filter[score][gte]=80`, `filter[title][contains]=예시`
- 정렬: `sort=-score,title`
- 포함 관계: `include=category,tags`
- 페이지: `page[number]=1&page[size]=20`이며 한 페이지는 최대 100개입니다.
- 총 건수: `page[totals]=true`를 보낸 요청만 `meta.totalCount`와 `links.last`를 받습니다.
- 커서 페이지: `page[after]` / `page[before]`로 OFFSET 없이 앞뒤로 이동합니다. 빈 값(`page[after]=`)은 컬렉션의 시작, `page[before]=`는 끝을 가리키므로 커서 모드의 진입점으로 사용합니다.
- related 자원 URL: to-many인 `GET /api/v1/examples/{id}/tags`는 `page[number]`/`page[size]`만 지원하고 한 페이지는 동일하게 최대 100개이며 `meta.totalCount`와 페이지 링크를 함께 반환합니다. `filter`·`sort`·`include`는 지원하지 않고, to-one인 `GET /api/v1/examples/{id}/category`는 모든 조회 파라미터를 거부합니다.

지원 필드와 연산자는 `app/schemas/example.py`의 `EXAMPLE_QUERY_POLICY`에서 명시적으로 허용합니다. 알 수 없는 파라미터나 허용되지 않은 필드·연산자는 JSON:API 오류로 거부합니다.

## 목록 페이지네이션과 링크 계약

목록 응답은 COUNT 쿼리를 기본으로 실행하지 않습니다. 다음 페이지 존재 여부는 요청한 크기보다 한 행 더 읽어서(probe) 판정하므로 `next`·`prev`는 언제나 총 건수 없이 계산됩니다.

- 기본(offset) 모드: `self`·`first`·`prev`·`next`는 `page[number]` 링크입니다. `page[totals]=true`가 없으면 `meta.totalCount`가 응답에 없고 `links.last`는 `null`입니다. `page[totals]=true`를 보내면 COUNT 한 번이 추가되어 `meta.totalCount`와 `last`가 채워지고, 모든 링크가 `page[totals]=true`를 그대로 유지합니다.
- 커서(keyset) 모드: `page[after]` 또는 `page[before]`가 있으면 OFFSET 대신 정렬 키 비교로 페이지를 자릅니다. 링크는 `self`(현재 커서), `first`(`page[after]=`), `last`(`page[before]=`), `next`(`page[after]=<커서>`), `prev`(`page[before]=<커서>`)로 구성되며 COUNT는 여전히 실행되지 않습니다. `last`는 마지막 `page[size]`개 행의 창을 가리키므로 앞에서부터 걸어온 마지막 페이지와 겹칠 수 있습니다.
- 커서는 요청의 유효 정렬(기본 정렬 또는 `sort` + tie breaker)에 묶입니다. 정렬을 바꾼 뒤 이전 커서를 재사용하거나, 손상된 커서, `page[after]`와 `page[before]` 동시 사용, 커서와 `page[number]` 동시 사용은 모두 400 `INVALID_PAGE`입니다.
- 커서는 NULL을 허용하지 않는 정렬 컬럼에서만 동작합니다. NULL이 섞인 컬럼은 keyset 비교로 도달할 수 없어 행을 건너뛰므로 `INVALID_PAGE`로 거부합니다.
- 하위 호환: `page[number]`/`page[size]`는 그대로 동작합니다. 다만 `meta.totalCount`와 `links.last`는 이제 `page[totals]=true`를 보내야 받을 수 있습니다.

## 비동기 작업

Compose 밖에서 worker를 실행할 때는 API와 같은 `DATABASE_URL` 및 `REDIS_URL`을 설정한 뒤 다음 명령을 사용합니다. 두 값 모두 암묵적인 기본값이 없으므로 누락하면 worker가 시작되지 않습니다.

```bash
uv run dramatiq app.jobs
```

Example actor는 CRUD에서 자동 enqueue되지 않습니다. side effect가 필요한 도메인 지점에서 명시적으로 호출합니다.

```python
from app.jobs import process_example

process_example.send(str(example.id))
```

actor는 잘못된 UUID와 존재하지 않는 Example을 경고로 남기고 종료합니다. 일시적인 DB 오류는 최대 세 번 재시도하며 Example의 공개 필드는 변경하지 않습니다.

### 만료 refresh 세션 정리

`refresh_sessions`에는 로그인과 회전마다 행이 쌓이고 로그아웃은 `revoked_at`만 표시하므로, 보존 기간이 지난 행은 `purge_expired_refresh_sessions` actor로 정리합니다.

```python
from app.jobs import purge_expired_refresh_sessions

purge_expired_refresh_sessions.send()
```

- 삭제 대상은 `expires_at`이 `REFRESH_SESSION_RETENTION_SECONDS`보다 더 오래 지난 행뿐입니다. 아직 유효한 세션과 방금 폐기된 세션은 보존 기간 값과 무관하게 남습니다.
- `REFRESH_SESSION_RETENTION_SECONDS`의 기본값은 `604800`초(7일)이고 worker 프로세스만 사용합니다. 음수를 주면 `REFRESH_SESSION_RETENTION_SECONDS must be non-negative`로 실패합니다.
- 삭제는 오래된 순서로 배치마다 commit하며 잠긴 행은 건너뜁니다. 그래서 로그인과 회전이 삭제 대상 행에 잡는 잠금 뒤에 줄 서지 않습니다.
- 다만 `SKIP LOCKED`는 이 statement가 고르는 행에만 적용됩니다. 삭제된 행을 가리키던 회전 chain 행의 `replaced_by_id`를 비우는 `ON DELETE SET NULL` cascade는 별도 행 잠금을 잡으므로, 각 배치는 짧은 `lock_timeout` 아래에서 실행됩니다. 경합하면 그 배치는 무한정 기다리지 않고 실패하고 Dramatiq가 actor를 재시도하며, 이미 commit된 배치는 그대로 남습니다.
- 보존 기간까지 지난 refresh token을 제시하면 오류 코드가 `TOKEN_EXPIRED`에서 `INVALID_TOKEN`으로 바뀝니다. 상태 코드는 401로 같습니다.

Dramatiq에는 내장 스케줄러가 없고 이 템플릿은 스케줄러 의존성을 추가하지 않습니다. 권장 방식은 외부 cron이 enqueue만 하고 실행은 상시 worker가 담당하는 형태 하나입니다.

```cron
0 * * * * cd /path/to/project && docker compose exec -T api python -c "from app.jobs import purge_expired_refresh_sessions; purge_expired_refresh_sessions.send()"
```

`cd`는 Compose 프로젝트 디렉터리(= `docker-compose.yml`이 있는 곳)입니다. runtime image에는 uv가 없으므로 image 안에서는 `uv run`이 아니라 image의 해석기인 `python`을 그대로 씁니다. `api` 서비스에는 이미 `REDIS_URL`이 설정되어 있어 enqueue가 Compose 네트워크의 broker에 도달합니다. Compose 밖에서 실행한다면 `REDIS_URL`이 가리키는 broker에 접근할 수 있어야 하며, 기본 Compose 스택은 redis를 호스트로 publish하지 않습니다.

## 오류 언어

오류 응답은 `Accept-Language`의 `ko`와 `en`을 지원하며 기본값은 한국어입니다.

```bash
curl \
  --header 'Accept: application/vnd.api+json' \
  --header 'Accept-Language: en' \
  http://localhost:4000/api/v1/examples/00000000-0000-4000-8000-000000000000
```

## 새 리소스 추가

이 템플릿은 리소스를 자동 탐색하지 않습니다. 새 리소스는 아래 7개 지점을 직접 만들고 `config/routes.py`에서 명시적으로 연결해야 동작합니다. 각 단계의 기준 구현은 저장소에 이미 있는 `Example` 리소스입니다.

### 1. 모델

`app/models/<resource>.py`에 SQLAlchemy 모델과 관계, `__table_args__`(제약과 인덱스)를 선언하고 `app/models/__init__.py`의 export를 갱신합니다. 기준 구현은 `app/models/example.py`이고, 다대다 연결 테이블은 `app/models/example_tag.py`를 따릅니다.

### 2. 마이그레이션

```bash
uv run alembic revision --autogenerate -m "create <resource>"
uv run alembic upgrade head
uv run alembic downgrade -1
```

`db/migrations/versions/`에 생성된 파일을 직접 검토해 `upgrade()`와 `downgrade()`를 모두 손으로 마무리합니다. autogenerate는 인덱스와 제약을 자주 놓치고, 되돌릴 수 없는 마이그레이션은 받지 않으므로 `downgrade()`를 비워 두지 않습니다. 기준 구현은 `db/migrations/versions/20260714_0001_create_example_resources.py`이며, PostgreSQL 전용 템플릿이므로 SQLite 호환을 위한 우회는 넣지 않습니다.

### 3. 쓰기 스키마와 조회 정책

`app/schemas/<resource>.py`에 Create/Update/Replace 쓰기 스키마, 관계 linkage 스키마, `QueryPolicy`를 선언하고 `app/schemas/__init__.py`의 export를 갱신합니다. 쓰기 스키마는 `app/jsonapi/naming.py`의 `JsonApiWriteSchema`를 상속해 `extra="forbid"`·camelCase alias·`strict=True`를 한 번에 받습니다. 기준 구현은 `app/schemas/example.py`의 `ExampleCreate`와 `EXAMPLE_QUERY_POLICY`입니다.

### 4. 시리얼라이저

`app/serializers/<resource>_serializer.py`에 `type_name`, `resource_path`, `attributes`, `relationships`를 선언하고 `app/serializers/__init__.py`의 export를 갱신합니다. 공개 응답 필드는 여기에서만 정합니다. 기준 구현은 `app/serializers/example_serializer.py`입니다.

### 5. 컨트롤러

`app/controllers/api/v1/<resource>s_controller.py`에 `CrudActions`를 상속한 선언만 둡니다. `model_class`, `serializer_class`, `query_policy`가 필수이고, 세 개의 쓰기 스키마는 `enable_writes`가 참인 자원에만 필요합니다. `relationships_schema`는 쓰기 가능한 관계가 있을 때, `enable_upsert`, `enable_writes`, `write_dependencies`는 필요한 자원에만 붙입니다. 읽기 전용 자원의 기준 구현은 `app/controllers/api/v1/example_categories_controller.py`입니다. 이어서 `app/controllers/api/v1/__init__.py`의 export를 갱신합니다 — `config/routes.py`가 이 패키지에서 import하므로 빠뜨리면 등록 자체가 되지 않습니다. 기준 구현은 `app/controllers/api/v1/examples_controller.py`입니다.

### 6. 라우트 등록

`config/routes.py`에서 컨트롤러 인스턴스를 모듈 변수로 만들고 `api_router.include_router(...)`로 등록합니다. 라우터를 자동으로 찾아 붙이는 장치는 없으며, 등록하지 않은 컨트롤러는 존재하지 않는 것과 같습니다.

### 7. 테스트

`tests/test_<resource>_controller.py`를 추가해 실제 route 조립, OpenAPI, 대표 성공·거부 응답을 확인합니다. 앱과 클라이언트는 `tests/conftest.py`의 공용 fixture(`app`, `client`, `authenticated_client`, `jsonapi_headers`)를 사용하고 조립을 다시 쓰지 않습니다. 기준 구현은 `tests/test_example_controller.py`입니다.

### 이름 규칙과 조용한 실패

| 선언 | 규칙 | 어겼을 때 |
| --- | --- | --- |
| `type_name` | camelCase 복수형(`examples`) | 검증 지점이 없습니다. `app/serializers/base.py`의 `ClassVar` 선언일 뿐이고 요청 문서의 `data.type`을 비교하는 기준값으로만 쓰이므로, 잘못 지어도 예외가 아니라 "다른 공개 계약"이 됩니다. |
| `resource_path` | `config/routes.py`에서 쓴 prefix와 문자열까지 동일 | `app/controllers/concerns/crud_base.py`의 `resource_path = self.serializer_class.resource_path or self.prefix`는 불일치를 감지하지 않습니다. 잘못된 `self` 링크와 `POST`·`PUT`의 `Location` 헤더가 조용히 나갑니다. |
| serializer `relationships` 키 | `relationships_schema`의 필드 alias와 동일 | `app/controllers/concerns/route_registrar.py`가 두 이름의 교집합만 등록하고 나머지는 건너뜁니다. 오타가 나면 예외 없이 쓰기 relationship route가 사라집니다. |

반대로 조용하지 않은 것도 있습니다. 관계의 ORM 매핑과 cardinality, `QueryPolicy.includes`의 include 경로는 `CrudActions.__init__`이 `serializer_class.loader_options(...)`를 호출하는 앱 조립 시점(= `config/routes.py` import 시점)에 예외가 됩니다. prefix 형식(`/`로 시작하고 `/`로 끝나지 않음)도 `validate_route_prefix`가 즉시 거부합니다.

위 표의 세 규칙은 `tests/config/test_routes.py`가 `config/routes.py`에 실제로 조립된 컨트롤러만 순회하며 확인합니다. 새 리소스를 등록하면 별도 작업 없이 이 검사에 함께 들어갑니다.

### 함께 갱신해야 하는 고정 기대값

새 리소스는 다음 세 가지 하드코딩된 기대값을 반드시 깨뜨립니다. 실패는 회귀가 아니라 갱신 신호입니다.

- `tests/test_example_controller.py`의 `test_openapi_exposes_only_declared_application_operations`와 `test_application_exposes_only_explicitly_composed_routes`는 앱이 노출하는 OpenAPI 경로 집합과 route 집합을 통째로 고정합니다.
- 새 마이그레이션이 head가 되면 `tests/config/test_migrations.py`의 head revision 기대값을 갱신합니다.
- 테이블을 추가하거나 지우면 `tests/integration/test_migration.py`의 table-set 기대값을 갱신합니다.

### 확인 명령

```bash
uv run pytest --no-cov tests/controllers -q
uv run pytest --no-cov tests/models tests/serializers tests/controllers tests/test_*_controller.py -q
uv run pytest --no-cov tests/config tests/integration -q
./scripts/check.sh
```

첫 줄은 공통 CRUD·관계 액션 계약만, 둘째 줄은 새 자원과 공개 route 회귀까지, 셋째 줄은 마이그레이션을 포함했을 때 실행합니다. 병합 전에는 `./scripts/check.sh`로 전체 게이트를 통과시킵니다. 같은 단계를 `uv run poe test-controllers`, `uv run poe test-db`, `uv run poe check`로도 실행할 수 있습니다.

계층별 상세 규칙은 `AGENTS.md`(저장소 전반), `app/AGENTS.md`(모델·스키마·시리얼라이저·컨트롤러), `tests/AGENTS.md`(테스트 배치와 fixture)에 있습니다.

## 검증

전체 백엔드 검사는 격리된 실제 PostgreSQL 테스트 DB를 자동으로 실행하고 정리합니다. 아래 명령으로 의존성 lock, 정적 검사·테스트·비밀 정보, Compose 설정, production runtime image와 전체 서비스 기동을 확인합니다.

```bash
uv sync --frozen
./scripts/check.sh
docker compose config --quiet
docker build --target runtime --tag template-python-fastapi:verify .
docker compose up -d --build --wait
docker compose down -v
```

검사 범위는 Ruff 린트·포맷, strict mypy, pytest와 커버리지, 비밀 정보 탐지입니다. mypy는 `app`, `config`, `db`뿐 아니라 `tests`까지 `strict`로 검사합니다.

개별 단계는 `poethepoet` 태스크로도 실행합니다. 태스크 정의는 `pyproject.toml`의 `[tool.poe.tasks]`에 있습니다.

```bash
uv run poe lint          # ruff check .
uv run poe format        # ruff format .
uv run poe typecheck     # mypy . (app, config, db, tests)
uv run poe test          # pytest (커버리지 게이트 포함)
uv run poe test-jsonapi  # pytest --no-cov tests/jsonapi -q
uv run poe migrate       # alembic upgrade head
uv run poe seed          # python -m db.seeds
uv run poe check         # ./scripts/check.sh 전체 게이트
```

`check`는 `scripts/check.sh`를 호출만 하므로 CI와 로컬 게이트가 갈라지지 않습니다. 다만 그 스크립트는 bash이므로 Windows에서는 Git Bash 또는 WSL이 필요하고, 나머지 태스크는 bash 없이 동작합니다.

커밋 시점에도 타입 오류를 잡도록 `.pre-commit-config.yaml`에 프로젝트 가상환경을 그대로 쓰는 `mypy` 훅(`language: system`, `entry: uv run mypy .`)을 등록했습니다.
