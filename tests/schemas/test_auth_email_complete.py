"""Shared email-validator edge vectors, including post-casefold storage bounds."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.auth import CredentialsAttributes, normalize_email

VECTORS = json.loads((Path(__file__).parents[1] / "fixtures/auth-email-vectors.json").read_text())


@pytest.mark.parametrize("vector", VECTORS)
def test_email_contract(vector: dict[str, object]) -> None:
    attributes = {"email": vector["input"], "password": "a-secure-password"}
    if vector["normalized"] is None:
        with pytest.raises(ValidationError):
            CredentialsAttributes.model_validate(attributes)
    else:
        result = CredentialsAttributes.model_validate(attributes)
        assert normalize_email(str(result.email)) == vector["normalized"]
