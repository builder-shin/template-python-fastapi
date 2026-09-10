<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# Serializer 회귀 테스트 지침

## 목적과 주요 파일

serializer가 선언한 공개 JSON:API 표현과 include 순회, 관계 로딩 경계를 검증한다. DB 없는 graph 사례와 실제 PostgreSQL의 persistent·detached 상태 회귀를 함께 둔다.

| 파일 | 역할 |
| --- | --- |
| `test_example_serializer.py` | Example·참조 자원 공개 표현, include graph, 일반/linkage-only preload, FK hint 검증과 lazy query 거부 |
| `test_auth_serializer.py` | User의 비밀 필드 제외와 고정 `/users/me` 링크, 링크 없는 `authTokens` 문서 |

## 두 종류의 검증

- serializer 단위 테스트는 실제 ORM과 분리한 test-only serializer graph로 include, cardinality, cycle, 값 encoding을 검증한다.
- test-only graph는 공개 contract의 최소 resource 구조만 표현하며, 실제 모델 규칙이나 controller 경로를 복제하지 않는다.
- cycle 사례는 재귀 종료와 중복 포함 방지를, cardinality 사례는 to-one·to-many linkage의 문서 모양을 확인한다.
- encoding 사례는 JSON에 안전한 공개 값으로 직렬화되는지 보되, 내부 객체 표현을 기대값으로 삼지 않는다.
- 단위 graph의 통과는 eager loading 보증이 아니므로 DB 기반 회귀를 별도로 유지한다.

## 실제 DB 기반 회귀

- 실제 resource 테스트는 type, id, attributes, relationships, links 등 외부에 약속한 public shape만 단언한다.
- relationship 기본값은 누락·null·빈 배열을 자원의 cardinality 계약에 맞춰 구분한다.
- `included`는 발견 순서에서 처음 나온 resource를 한 번만 포함하는지 확인한다.
- include 경로가 여럿일 때도 동일 resource의 중복과 cycle 재진입이 없는지 확인한다.
- eager loading 회귀는 serialization 동안 발생한 lazy query를 감시해, 허용된 preload 밖 query가 없음을 단언한다.
- 일반 loader의 완전한 관계 로딩과 `linkage_only` loader를 구별한다. category는 이미 로딩된 FK로 linkage를 만들고 tags는 ID만 로딩해도 같은 공개 문서를 만들어야 한다. include를 요청한 자원은 필요한 전체 표현을 로딩한다.
- category/tag 참조 자원도 정규 resource self link를 제공하는지 검사한다. category_id를 defer한 경우에는 FK hint가 몰래 lazy query를 실행하지 않고 로딩 오류를 내야 한다.

## FK linkage hint의 경계

- hint는 to-one 관계의 실제 mapped Column이어야 하며 대상 primary key를 가리키는 FK여야 한다. 관계 이름·미매핑 속성·일반 score column·잘못된 대상 column을 허용하지 않는다.
- 추가 조건이 있는 primaryjoin은 local/remote 쌍이 같더라도 hint로 관계 의미를 단순화하지 않는다. 잘못된 선언은 `JsonApiSerializationError`로 검증한다.
- 비ORM graph에서는 hint를 무시하고 일반 속성 접근을 사용한다. 사용할 FK 값이 없는 persistent·detached 객체는 기존 로딩 guard를 따른다. join 검증용 별도 mapped Base의 테이블은 생성하지 않는다.

## listener 수명과 작성 방식

- query 감시 event listener는 test의 `finally` 또는 동등한 정리 경로에서 항상 제거한다.
- listener가 남으면 다음 테스트의 query 수를 오염시키므로 fixture 간 공유 전역 listener를 만들지 않는다.
- DB test는 resource 생성, 필요한 preload, serialize, public document 검증의 순서를 명확히 드러낸다.
- include dedup 기대값은 입력 순서를 바꾸지 않고, 첫 발견 순서와 resource 식별자로 작성한다.
- public shape 변경은 단위 graph와 실제 DB 회귀 중 해당 계약을 검증하는 곳 모두를 검토한다.

## 실행

- 새 serializer 회귀는 graph 경계 또는 실제 공개 표현 중 어느 층을 보호하는지 파일명과 테스트명으로 드러낸다.
- lazy query 검증은 query 총량의 우연한 숫자보다 serialization에서 발생한 비허용 query의 부재를 확인한다.
- serializer가 오류를 표현할 때도 내부 ORM 객체나 예외 문자열을 API 계약으로 고정하지 않는다.
- 실행 명령: `uv run pytest --no-cov tests/serializers -q`. `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리키는 상태에서 실행한다. URL이 없으면 `./scripts/check.sh`로 임시 Docker PostgreSQL 전체 게이트를 사용한다.

## 공통 패턴과 의존성

- include 미요청의 member 누락과 요청했지만 결과가 없는 `included: []`를 구별한다. primary resource는 `included`에 반복하지 않는다.
- transient·pending 객체의 생략된 관계 기본값과 persistent·detached 객체의 미로딩 관계를 구별한다. 후자는 유효한 로딩된 FK hint가 있는 to-one linkage만 처리할 수 있으며, 필요한 데이터가 없으면 lazy query 없이 실패해야 한다.
- UUID·시각은 고정값으로 JSON 표현을 단언한다. camelCase 공개 이름과 `resource_location()`이 만드는 resource·relationship 링크를 함께 검토한다.
- 인증 serializer는 `password_hash`를 공개하지 않고, 발급 token DTO는 계약상 access/refresh token과 만료 정보만 노출한다.
- 내부: `app/serializers`, Example·User ORM, `app/auth/tokens.py`의 `AuthTokenResource`, 상위 DB fixture. 외부: pytest, SQLAlchemy event·selectinload, Python dataclass·UUID·datetime.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
