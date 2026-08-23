"""PostgreSQL upsert action and the SQLAlchemy state handling it depends on.

Third layer of the ``CrudActions`` chain. ``upsert`` is kept in one piece on purpose:
``persisted_values`` and ``relationships_to_reapply`` are locals of the
``session.no_autoflush`` block that the code after it reuses, so splitting the body
further would require a result object and would move the advisory-lock and
``INSERT ... ON CONFLICT DO UPDATE`` boundaries around.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import blake2b
from typing import Any, cast

from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import RowMapping, func, literal_column, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, make_transient_to_detached
from sqlalchemy.orm.attributes import set_committed_value

from app.controllers.concerns.document_parsing import (
    document_attributes,
    document_data,
    document_relationships,
    reject_query_parameters,
    require_document_id,
    require_resource_type,
)
from app.controllers.concerns.relationship_resolver import CrudRelationships
from app.jsonapi import JsonApiResponse
from app.jsonapi.naming import snake_to_camel

# Labels the ``xmax = 0`` discriminator added to the upsert statement's ``RETURNING``
# list. It is not a mapped column, so ``_apply_returned_columns`` and
# ``_materialize_created_relationships`` both ignore it.
_WAS_INSERTED = "__jsonapi_was_inserted__"


class CrudUpsert[
    ModelT,
    CreateT: BaseModel,
    UpdateT: BaseModel,
    ReplaceT: BaseModel,
](CrudRelationships[ModelT, CreateT, UpdateT, ReplaceT]):
    """Own the PUT action and the relationship bookkeeping it needs."""

    def upsert(
        self,
        resource_id: str,
        request: Request,
        document: BaseModel,
        session: Session,
    ) -> JsonApiResponse:
        """Atomically create or fully replace one resource using PostgreSQL."""

        reject_query_parameters(request)
        data = document_data(document)
        require_resource_type(data, expected_type=self.serializer_class.type_name)
        require_document_id(data, resource_id)
        attributes = cast(ReplaceT, document_attributes(data))
        relationships = document_relationships(data)
        coerced_id = self._coerce_resource_id(resource_id)
        mapper = self._model_mapper()
        primary_key = mapper.primary_key[0]

        with session.begin():
            self._lock_upsert_resource(session, coerced_id)
            existing = self._find_resource_or_none(session, resource_id)
            created = existing is None
            model = existing if existing is not None else self.model_class()
            if created:
                setattr(model, primary_key.key, coerced_id)
                self.serializer_class.initialize_relationship_defaults(model)
                for definition in self.serializer_class.relationships.values():
                    set_committed_value(model, definition.attribute, getattr(model, definition.attribute))

            model_values = dict(self.model_params(attributes, exclude_unset=False))
            with session.no_autoflush:
                for name, value in model_values.items():
                    setattr(model, name, value)
                if created:
                    self.assign_relationships(session, model, relationships)
                else:
                    self._reset_omitted_relationships(model, relationships)
                    self.assign_relationships(session, model, relationships)
                self.before_upsert(session, model, attributes)

                relationships_to_reapply = self._relationships_to_reapply(model, relationships) if created else {}
                if created:
                    self._clear_transient_relationship_backrefs(model, relationships_to_reapply)

                state = sqlalchemy_inspect(model)
                if state is None:
                    raise RuntimeError("upsert resource is not mapped")
                persisted_names = set(model_values)
                persisted_names.update(
                    attribute.key
                    for attribute in mapper.column_attrs
                    if attribute.key != primary_key.key and state.attrs[attribute.key].history.has_changes()
                )
                persisted_values = {name: getattr(model, name) for name in persisted_names}
                insert_statement = postgresql_insert(mapper.local_table).values(
                    **{primary_key.key: coerced_id, **persisted_values}
                )
                update_values: dict[str, object] = dict(persisted_values)
                if "updated_at" in mapper.local_table.c and "updated_at" not in update_values:
                    update_values["updated_at"] = func.now()
                upsert_statement = insert_statement.on_conflict_do_update(
                    index_elements=[primary_key],
                    set_=update_values,
                )
                # ``xmax`` is zero exactly on the tuple versions this statement inserted, so
                # it reports which branch of ``ON CONFLICT`` PostgreSQL really took.
                returned = (
                    session.execute(
                        upsert_statement.returning(
                            *mapper.local_table.c,
                            (literal_column("xmax") == 0).label(_WAS_INSERTED),
                        )
                    )
                    .mappings()
                    .one()
                )

            if created and not returned[_WAS_INSERTED]:
                # The pre-check said "create" but the statement updated an existing row, so a
                # concurrent transaction committed that row - and possibly rows referencing it -
                # in between. Nothing staged here describes committed state any more, so the
                # resource is read back and the requested relationship values re-applied on it.
                model = self._find_resource(session, resource_id)
                for public_name, value in relationships_to_reapply.items():
                    definition = self._relationship_definition(public_name)
                    setattr(model, definition.attribute, value)
            else:
                if created:
                    # The staged candidate carries the row that was just inserted, so it is
                    # promoted in place instead of being read back a second time.
                    make_transient_to_detached(model)
                    session.add(model)
                self._apply_returned_columns(model, mapper, returned)
                if created:
                    self._materialize_created_relationships(session, model, relationships_to_reapply, returned)

            self.after_upsert(session, model, attributes)
            session.flush()
            identifier = self._model_identity(model)
            headers = {"Location": self._canonical_resource_location(identifier)} if created else None
            response = JsonApiResponse(
                self.serializer_class.document(model),
                status_code=201 if created else 200,
                headers=headers,
            )

        return response

    def _lock_upsert_resource(self, session: Session, resource_id: object) -> None:
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            raise RuntimeError("CrudActions upsert requires PostgreSQL")
        mapper = self._model_mapper()
        lock_material = f"{mapper.local_table.fullname}:{resource_id}".encode()
        lock_key = int.from_bytes(blake2b(lock_material, digest_size=8).digest(), signed=True)
        session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    def _apply_returned_columns(
        self,
        model: ModelT,
        mapper: Any,
        returned: RowMapping,
    ) -> None:
        """Fill committed column state from the upsert statement's ``RETURNING`` row."""

        for attribute in mapper.column_attrs:
            column_key = cast(str, attribute.expression.key)
            if column_key in returned:
                set_committed_value(model, attribute.key, returned[column_key])

    def _materialize_created_relationships(
        self,
        session: Session,
        model: ModelT,
        relationships_to_reapply: Mapping[str, object],
        returned: RowMapping,
    ) -> None:
        """Load every serializer-declared relationship of a freshly inserted resource.

        The create path fills column state from ``RETURNING`` instead of reading the row
        back, so no declared relationship may be left unloaded: the serializer refuses to
        emit linkage it would have to lazy-load.
        """

        mapper = self._model_mapper()
        for public_name, definition in self.serializer_class.relationships.items():
            if public_name in relationships_to_reapply:
                # Re-applying against an empty committed value recreates the history the
                # flush needs to write the foreign key and the association rows.
                set_committed_value(model, definition.attribute, [] if definition.many else None)
                setattr(model, definition.attribute, relationships_to_reapply[public_name])
                continue
            if definition.many:
                # The row was just inserted, so no association row exists that this
                # transaction did not create itself.
                set_committed_value(model, definition.attribute, [])
                continue
            # The join is resolved through ``local_remote_pairs`` rather than by treating the
            # local column as a foreign key into the target's primary key: that assumption is
            # false for a relationship whose foreign key lives on the remote side, where the
            # local column is this row's own primary key and would fetch an unrelated row.
            pairs = mapper.relationships[definition.attribute].local_remote_pairs or ()
            local_values = {local: returned.get(cast(str, local.key)) for local, _ in pairs}
            target: object | None = None
            if pairs and all(value is not None for value in local_values.values()):
                statement = select(self._relationship_target_model(definition)).where(
                    *(remote == local_values[local] for local, remote in pairs)
                )
                target = session.scalars(statement).one_or_none()
            set_committed_value(model, definition.attribute, target)

    def _relationships_to_reapply(
        self,
        model: ModelT,
        relationships: BaseModel | None,
    ) -> dict[str, object]:
        requested = self._provided_relationship_names(relationships)
        state = sqlalchemy_inspect(model, raiseerr=False)
        if state is None:
            raise RuntimeError("upsert relationship resource is not mapped")
        values: dict[str, object] = {}
        for public_name, definition in self.serializer_class.relationships.items():
            value = getattr(model, definition.attribute)
            changed = state.attrs[definition.attribute].history.has_changes()
            non_default = bool(value) if definition.many else value is not None
            if public_name in requested or changed or non_default:
                values[public_name] = list(value) if definition.many else value
        return values

    def _clear_transient_relationship_backrefs(
        self,
        model: ModelT,
        relationship_values: Mapping[str, object],
    ) -> None:
        """Detach a staged upsert candidate from persistent reverse collections."""

        mapper = self._model_mapper()
        for public_name, value in relationship_values.items():
            definition = self._relationship_definition(public_name)
            relationship = mapper.relationships[definition.attribute]
            reverse_name = relationship.back_populates
            targets = list(cast(Sequence[object], value)) if definition.many else ([] if value is None else [value])
            if reverse_name is not None:
                for target in targets:
                    if not self._reverse_side_may_hold(target, reverse_name):
                        continue
                    target_mapper = sqlalchemy_inspect(type(target))
                    if target_mapper is None:
                        raise RuntimeError("relationship target model is not mapped")
                    reverse = target_mapper.relationships[reverse_name]
                    reverse_value = getattr(target, reverse_name)
                    if reverse.uselist:
                        while model in reverse_value:
                            reverse_value.remove(model)
                    elif reverse_value is model:
                        setattr(target, reverse_name, None)
            set_committed_value(model, definition.attribute, value)

    @staticmethod
    def _reverse_side_may_hold(target: object, reverse_name: str) -> bool:
        """Tell whether reading the reverse side can observe the staged candidate.

        Reading it otherwise would load the target's whole reverse collection - every row
        pointing at that target - inside the advisory-lock window. A not-yet-inserted
        object can only be there when a backref event put it there, and such an event on an
        unloaded attribute records a pending mutation instead of loading it.
        """

        target_state = sqlalchemy_inspect(target, raiseerr=False)
        if target_state is None:
            raise RuntimeError("relationship target model is not mapped")
        pending_mutations = cast(Mapping[str, object], target_state._pending_mutations)
        return reverse_name in target_state.dict or reverse_name in pending_mutations

    @staticmethod
    def _provided_relationship_names(relationships: BaseModel | None) -> set[str]:
        provided: set[str] = set()
        if relationships is None:
            return provided
        for field_name, field in type(relationships).model_fields.items():
            if field_name in relationships.model_fields_set:
                provided.add(field.alias or snake_to_camel(field_name))
        return provided

    def _reset_omitted_relationships(
        self,
        model: ModelT,
        relationships: BaseModel | None,
    ) -> None:
        provided = self._provided_relationship_names(relationships)
        for public_name, definition in self.serializer_class.relationships.items():
            if public_name in self._writable_relationship_names and public_name not in provided:
                setattr(model, definition.attribute, [] if definition.many else None)
