# Python FastAPI Template

FastAPI, SQLAlchemy 2, PostgreSQL, Alembic으로 구성한 JSON:API 1.1 템플릿입니다. 자체 JWT 인증과 Redis 기반 Dramatiq worker를 제공하며, Rails 컨트롤러의 공통 CRUD 액션과 비슷하게 `CrudActions`를 상속합니다. 도메인 컨트롤러에는 모델·스키마·시리얼라이저·조회 정책만 선언합니다.

## 구조

```text
app/controllers/concerns/crud_actions.py       # Rails형 공통 CRUD·관계 액션
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
uv run alembic upgrade head
uv run python -m db.seeds
uv run uvicorn config.asgi:application --reload --port 4000
```

기본 접속 정보는 `.env.example`과 같습니다. 다른 값을 사용할 때는 `DATABASE_URL`과 풀 설정을 셸 환경 변수로 내보낸 뒤 명령을 실행합니다.

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

## 조회 파라미터

- 필터: `filter[status]=active`, `filter[score][gte]=80`, `filter[title][contains]=예시`
- 정렬: `sort=-score,title`
- 포함 관계: `include=category,tags`
- 페이지: `page[number]=1&page[size]=20`이며 한 페이지는 최대 100개입니다.

지원 필드와 연산자는 `app/schemas/example.py`의 `EXAMPLE_QUERY_POLICY`에서 명시적으로 허용합니다. 알 수 없는 파라미터나 허용되지 않은 필드·연산자는 JSON:API 오류로 거부합니다.

## 비동기 작업

Compose 밖에서 worker를 실행할 때는 API와 같은 `DATABASE_URL` 및 `REDIS_URL`을 설정한 뒤 다음 명령을 사용합니다.

```bash
uv run dramatiq app.jobs
```

Example actor는 CRUD에서 자동 enqueue되지 않습니다. side effect가 필요한 도메인 지점에서 명시적으로 호출합니다.

```python
from app.jobs import process_example

process_example.send(str(example.id))
```

actor는 잘못된 UUID와 존재하지 않는 Example을 경고로 남기고 종료합니다. 일시적인 DB 오류는 최대 세 번 재시도하며 Example의 공개 필드는 변경하지 않습니다.

## 오류 언어

오류 응답은 `Accept-Language`의 `ko`와 `en`을 지원하며 기본값은 한국어입니다.

```bash
curl \
  --header 'Accept: application/vnd.api+json' \
  --header 'Accept-Language: en' \
  http://localhost:4000/api/v1/examples/00000000-0000-4000-8000-000000000000
```

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

검사 범위는 Ruff 린트·포맷, strict mypy, pytest와 커버리지, 비밀 정보 탐지입니다.
