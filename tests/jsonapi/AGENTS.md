# JSON:API 프로토콜 테스트 지침

## 문서 모델과 schema

- 다섯 파일은 문서 모델 검증과 JSON schema 검증을 함께 유지한다. 한쪽 통과만으로 protocol 적합성을 주장하지 않는다.
- `data: null`, `data` 누락, `MISSING`과 오류 문서는 서로 다른 경우이므로 예외적으로 합치지 않는다.
- resource, relationship linkage, `included`, error document의 허용·필수 조합을 정상과 거부 요청으로 짝지어 검증한다.
- `included`에 없는 linkage, 부적절한 null, data와 errors의 충돌은 공개 오류 계약으로 단언한다.
- schema 실패는 status뿐 아니라 JSON:API 오류의 code와 source를 확인해 원인을 드러낸다.

## 협상과 오류 출처

- `Accept` 테스트는 vendor media type의 specificity와 `q` 값을 교차해, 선택된 표현과 거부 결과를 명확히 단언한다.
- `Content-Type`의 허용 profile과 거부 profile을 분리하고, 다른 media type의 느슨한 수용을 추가하지 않는다.
- 한국어·영어 오류 catalog는 같은 code와 source 구조, 각 언어에 맞는 상세 문구를 모두 확인한다.
- body pointer와 query parameter source, header source는 서로 바꾸어 단언하지 않는다.
- header rewrite와 exception handler 순서는 실제 응답의 status·header·오류 문서로 회귀를 고정한다.

## query와 링크

- 허용 query는 parser 결과뿐 아니라 route 응답을 검증하고, 거부 query는 원 요청 parameter를 `source.parameter`에 보존한다.
- allowlist 밖 filter, sort, include, page, `fields[...]`가 통과하지 않는 사례를 유지한다.
- wildcard filter·include를 편의상 주입하거나, 거부 입력을 조용히 삭제하는 기대값을 만들지 않는다.
- 정렬은 동점 상황에서도 결정적 순서를, pagination은 문서와 links의 일관된 경계를 단언한다.
- pagination links는 허용된 원 query를 보존하되, 잘못된 값을 정상 link로 되살리지 않는지 확인한다.

## 작성과 실행

- 하나의 테스트는 문서 불변조건, 협상, query, 오류 localization 중 하나의 주된 실패 원인을 드러내도록 좁힌다.
- status code만 검사하는 protocol 테스트는 추가하지 않는다. 필요한 header와 document 모양을 함께 확인한다.
- 예외 메시지 문자열보다 안정적인 code·source를 우선하고, 언어별 문구는 catalog 계약일 때만 정확히 고정한다.
- 새 query 문법은 수용, 거부, link 재현을 한 묶음으로 보강한다.
- 실행 명령: `uv run pytest --no-cov tests/jsonapi -q`
