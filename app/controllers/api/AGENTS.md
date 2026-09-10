<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-11 | Updated: 2026-09-11 -->

# 버전별 API Controller 지침

## Purpose

API controller를 버전별로 배치하는 패키지다. 이 디렉터리 자체에서 router를 만들거나 자동 등록하지 않는다.

## Key Files

| 파일 | 역할 |
| --- | --- |
| `__init__.py` | 버전별 controller 패키지 표시 |

## Subdirectories

| 경로 | 책임 |
| --- | --- |
| [v1/](v1/AGENTS.md) | Example CRUD, category·tag 참조 자원, 인증·현재 사용자 controller export |

## For AI Agents

### Working In This Directory

- 버전의 public controller는 해당 패키지 `__init__.py`에서 export하고 `config/routes.py`에서 명시적으로 import·조립한다.
- 새 버전이나 자원은 상위의 JSON:API 계약과 공통 concern을 재사용한다. 버전 폴더를 이유로 별도 CRUD·repository/service 계층을 만들지 않는다.
- URL prefix와 serializer의 canonical resource 경로를 함께 검토한다.

### Testing Requirements

버전 경로·조립 변경은 `create_app()` 기반 공개 controller 테스트와 OpenAPI를 확인한다. 좁은 pytest는 독립된 `*_test` PostgreSQL의 `TEST_DATABASE_URL`이 있을 때만 실행하고, 없으면 `./scripts/check.sh`를 사용한다. 구체적인 테스트 경로는 [v1 지침](v1/AGENTS.md)을 따른다.

### Common Patterns

현재 `v1`은 Auth·Examples·ExampleCategories·ExampleTags·Users 다섯 controller를 export하고 `config/routes.py`가 각 prefix·tag를 지정한다. 버전 패키지 import만으로 전역 route가 등록되게 하지 않는다.

## Dependencies

- 내부: [공통 concerns](../concerns/AGENTS.md), `config/routes.py`, 자원별 schema·serializer.
- 외부: 버전 controller에서 사용하는 FastAPI와 SQLAlchemy.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
