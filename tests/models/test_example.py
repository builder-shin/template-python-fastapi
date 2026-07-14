"""Example model tests."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.example import Example, ExampleStatus, example_tags
from app.models.example_category import ExampleCategory
from app.models.example_tag import ExampleTag


@pytest.mark.parametrize("score", [-1, 101])
def test_example_requires_score_in_range(db_session: Session, score: int) -> None:
    example = Example(title="invalid", status=ExampleStatus.DRAFT, score=score)
    db_session.add(example)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_example_uses_uuid_v4_and_timestamps(db_session: Session) -> None:
    example = Example(title="식별자", status=ExampleStatus.DRAFT, score=50)
    db_session.add(example)

    db_session.flush()

    assert isinstance(example.id, UUID)
    assert example.id.version == 4
    assert example.created_at is not None
    assert example.updated_at is not None


def test_example_relates_to_category_and_tags(db_session: Session) -> None:
    category = ExampleCategory(name="특허")
    tag = ExampleTag(name="출원")
    example = Example(
        title="관계",
        status=ExampleStatus.ACTIVE,
        score=80,
        category=category,
        tags=[tag],
    )
    db_session.add(example)

    db_session.flush()

    assert example.category is category
    assert example.tags == [tag]
    assert category.examples == [example]
    assert tag.examples == [example]


@pytest.mark.parametrize("model_type", [ExampleCategory, ExampleTag])
def test_category_and_tag_names_are_unique(
    db_session: Session,
    model_type: type[ExampleCategory] | type[ExampleTag],
) -> None:
    db_session.add_all([model_type(name="중복"), model_type(name="중복")])

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_example_tags_uses_composite_primary_key() -> None:
    assert [column.name for column in example_tags.primary_key.columns] == ["example_id", "tag_id"]


def test_deleting_example_cascades_only_association_rows(db_session: Session) -> None:
    tag = ExampleTag(name="보존")
    example = Example(
        title="삭제",
        status=ExampleStatus.ARCHIVED,
        score=70,
        tags=[tag],
    )
    db_session.add(example)
    db_session.flush()

    db_session.delete(example)
    db_session.flush()

    assert db_session.scalar(select(func.count()).select_from(example_tags)) == 0
    assert db_session.get(ExampleTag, tag.id) is tag
