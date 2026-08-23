"""Deterministic and idempotent database seed tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Example, ExampleCategory, ExampleStatus, ExampleTag
from db import seeds as seeds_module
from db.seeds import SEED_CATEGORY_ID, SEED_EXAMPLE_ID, SEED_TAG_ID, seed


def _counts(session: Session) -> tuple[int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(ExampleCategory)) or 0,
        session.scalar(select(func.count()).select_from(ExampleTag)) or 0,
        session.scalar(select(func.count()).select_from(Example)) or 0,
    )


def test_seed_is_deterministic_and_idempotent(committed_session: Session) -> None:
    seed(committed_session)
    committed_session.commit()
    committed_session.expire_all()
    first_counts = _counts(committed_session)
    first_category = committed_session.get(ExampleCategory, SEED_CATEGORY_ID)
    first_tag = committed_session.get(ExampleTag, SEED_TAG_ID)
    first_example = committed_session.get(Example, SEED_EXAMPLE_ID)
    assert first_category is not None
    assert first_tag is not None
    assert first_example is not None
    first_updated_at = (
        first_category.updated_at,
        first_tag.updated_at,
        first_example.updated_at,
    )
    committed_session.commit()

    seed(committed_session)
    committed_session.commit()
    committed_session.expire_all()

    assert first_counts == (1, 1, 1)
    assert _counts(committed_session) == first_counts
    category = committed_session.get(ExampleCategory, SEED_CATEGORY_ID)
    tag = committed_session.get(ExampleTag, SEED_TAG_ID)
    example = committed_session.get(Example, SEED_EXAMPLE_ID)
    assert category is not None
    assert tag is not None
    assert example is not None
    assert example.category_id == category.id
    assert [related.id for related in example.tags] == [tag.id]
    assert (category.updated_at, tag.updated_at, example.updated_at) == first_updated_at


def test_seed_repairs_drift_in_fixed_id_rows(committed_session: Session) -> None:
    stale_updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    committed_session.add_all(
        [
            ExampleCategory(
                id=SEED_CATEGORY_ID,
                name="오래된 카테고리",
                updated_at=stale_updated_at,
            ),
            ExampleTag(
                id=SEED_TAG_ID,
                name="오래된 태그",
                updated_at=stale_updated_at,
            ),
            Example(
                id=SEED_EXAMPLE_ID,
                title="오래된 제목",
                description=None,
                status=ExampleStatus.DRAFT,
                score=1,
                category_id=None,
                updated_at=stale_updated_at,
            ),
        ]
    )
    committed_session.commit()

    seed(committed_session)
    committed_session.commit()
    committed_session.expire_all()

    category = committed_session.get(ExampleCategory, SEED_CATEGORY_ID)
    tag = committed_session.get(ExampleTag, SEED_TAG_ID)
    example = committed_session.get(Example, SEED_EXAMPLE_ID)
    assert category is not None
    assert tag is not None
    assert example is not None
    assert category.name == "기본 카테고리"
    assert tag.name == "기본 태그"
    assert example.title == "JSON:API 예시"
    assert example.description == "JSON:API와 CRUD 동작을 확인하기 위한 기본 데이터입니다."
    assert example.status is ExampleStatus.ACTIVE
    assert example.score == 90
    assert example.category_id == SEED_CATEGORY_ID
    assert [related.id for related in example.tags] == [SEED_TAG_ID]
    assert category.updated_at > stale_updated_at
    assert tag.updated_at > stale_updated_at
    assert example.updated_at > stale_updated_at


def test_seed_leaves_transaction_ownership_to_the_caller(committed_session: Session) -> None:
    seed(committed_session)
    assert _counts(committed_session) == (1, 1, 1)

    committed_session.rollback()

    assert _counts(committed_session) == (0, 0, 0)


def test_seed_does_not_hide_natural_key_conflicts(committed_session: Session) -> None:
    committed_session.add(ExampleCategory(id=uuid4(), name="기본 카테고리"))
    committed_session.commit()

    with pytest.raises(IntegrityError, match="uq_categories_name"):
        seed(committed_session)

    committed_session.rollback()


def test_seed_main_runs_inside_a_factory_owned_transaction(
    committed_session: Session,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(seeds_module, "get_session_factory", lambda: factory)
    observed: list[Session] = []
    original_seed = seeds_module.seed

    def observing_seed(session: Session) -> None:
        observed.append(session)
        assert session.in_transaction()
        original_seed(session)

    monkeypatch.setattr(seeds_module, "seed", observing_seed)

    seeds_module.main()

    committed_session.expire_all()
    assert _counts(committed_session) == (1, 1, 1)
    assert len(observed) == 1
    assert not observed[0].in_transaction()
