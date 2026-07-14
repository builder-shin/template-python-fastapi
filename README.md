# Python FastAPI Template

FastAPI, SQLAlchemy 2, PostgreSQL, Alembic으로 구성한 JSON:API 1.1 템플릿입니다. Rails 컨트롤러의 공통 CRUD 액션과 비슷하게 `CrudActions`를 상속하고, 도메인 컨트롤러에는 모델·스키마·시리얼라이저·조회 정책만 선언합니다.

## 구조

```text
app/controllers/concerns/crud_actions.py       # Rails형 공통 CRUD·관계 액션
app/controllers/api/v1/examples_controller.py # 예시 리소스 선언
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

DB가 정상 상태가 되면 마이그레이션이 실행되고, 성공한 뒤 API가 시작됩니다.

```bash
docker compose up -d --build --wait
docker compose exec api python -m db.seeds
```

- API: `http://localhost:4000`
- OpenAPI 문서: `http://localhost:4000/api-docs`

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
  --data '{"data":{"type":"examples","attributes":{"title":"예시","status":"active","score":80}}}' \
  http://localhost:4000/api/v1/examples
```

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

## 오류 언어

오류 응답은 `Accept-Language`의 `ko`와 `en`을 지원하며 기본값은 한국어입니다.

```bash
curl \
  --header 'Accept: application/vnd.api+json' \
  --header 'Accept-Language: en' \
  http://localhost:4000/api/v1/examples/00000000-0000-4000-8000-000000000000
```

## 검증

전체 백엔드 검사는 격리된 PostgreSQL 테스트 DB를 자동으로 실행하고 정리합니다.

```bash
./scripts/check.sh
```

검사 범위는 Ruff 린트·포맷, strict mypy, pytest와 커버리지, 비밀 정보 탐지입니다.
