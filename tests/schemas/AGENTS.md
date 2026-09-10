<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 인증·Example 입력 schema 테스트

## 목적과 주요 파일

인증과 Example endpoint의 JSON:API 쓰기 문서에서 엄격한 타입·필드·문자열 범위, 별칭·누락 값과 이메일 정규화를 검증한다.

| 파일 | 역할 |
| --- | --- |
| `test_auth.py` | Register·Login·Refresh 문서 수용/거부, extra member·type·문자열 경계, 이메일 strip·casefold |
| `test_example.py` | 공통 strict schema, score·title·active·status 입력, update MISSING, 관계 쓰기의 최소 member |

## 작업 규칙과 공통 패턴

- 최소 유효 문서를 만드는 helper에서 자원 필드나 attributes를 바꾸어 정상/거부 입력을 짝지어 검사한다. Pydantic `model_validate()`와 `ValidationError`를 사용한다.
- auth·Example 문서는 공통 `JsonApiWriteSchema`와 `snake_to_camel`을 공유한다. strict·extra forbid·alias 설정을 자원마다 복제하지 않고 상속 계약을 확인한다.
- `users`, `authCredentials`, `refreshTokens`의 resource type을 구별한다. id·relationships·top-level meta·미선언 attributes의 수용 범위를 느슨하게 만들지 않는다.
- 이메일 형식·최대 길이와 비밀번호 12~128자 경계, 숫자를 문자열로 자동 변환하지 않는 계약을 유지한다.
- `refreshToken`은 비어 있지 않은 문자열이어야 하고 Python 속성은 `refresh_token`이다. schema가 JWT 서명·만료를 검사한다고 가정하지 않는다.
- 이메일 정규화 helper의 strip·casefold와 DB에 저장하는 시점은 구별한다. HTTP 오류 pointer와 transaction 내 정규화는 상위 인증 controller 테스트에서 확인한다.
- Example score의 숫자 문자열·실수·bool·범위 초과와 title·active의 잘못된 타입을 거부한다. enum 필드만 허용한 JSON 문자열은 `ExampleStatus` schema component를 유지해야 하며 전체 strict 검증을 느슨하게 만들지 않는다.
- update에서 생략한 속성은 `MISSING`이며 제공한 잘못된 타입은 그대로 거부한다. relationships 문서는 최소 한 member를 요구하는 계약을 유지한다.

## 검증

상위 실행 규칙에 따라 `TEST_DATABASE_URL`이 독립된 `*_test` PostgreSQL을 가리킬 때 `uv run pytest --no-cov tests/schemas tests/test_auth_controller.py tests/test_example_controller.py -q`를 실행한다. URL이 없으면 `./scripts/check.sh`의 임시 Docker PostgreSQL 전체 게이트를 사용한다. 이 디렉터리의 schema 테스트 자체는 DB fixture를 요청하지 않는다.

## 의존성

- 내부: `app/schemas/auth.py`·`example.py`, `app/jsonapi/naming.py`와 쓰기 문서 모델, ExampleStatus; HTTP 연결은 상위 auth·Example controller 테스트.
- 외부: pytest, Pydantic과 이메일 검증 의존성.

<!-- MANUAL: 이 아래에 추가한 수동 메모는 deepinit 갱신 시 보존한다. -->
