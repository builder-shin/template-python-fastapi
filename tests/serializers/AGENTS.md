# Serializer 회귀 테스트 지침

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
- 실행 명령: `uv run pytest --no-cov tests/serializers/test_example_serializer.py -q`
