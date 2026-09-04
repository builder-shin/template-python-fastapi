"""Example tag read-only JSON:API resource controller."""

from pydantic import BaseModel

from app.controllers.concerns import CrudActions
from app.models import ExampleTag
from app.schemas import EXAMPLE_TAG_QUERY_POLICY
from app.serializers import ExampleTagSerializer


class ExampleTagsController(CrudActions[ExampleTag, BaseModel, BaseModel, BaseModel]):
    """Expose example tags as a read-only collection.

    라벨은 서버가 관리하는 참조 데이터다. 쓰기 라우트를 열지 않는 이유와
    이 자원이 존재하는 이유는 ``ExampleCategoriesController``와 같다.
    """

    model_class = ExampleTag
    serializer_class = ExampleTagSerializer
    query_policy = EXAMPLE_TAG_QUERY_POLICY
    enable_writes = False
