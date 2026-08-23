"""Dramatiq broker configuration tests."""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from config import broker as broker_config


def test_configure_broker_sets_redis_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://queue.example:6380/4")
    redis_broker = MagicMock(name="redis_broker")
    redis_broker_factory = MagicMock(return_value=redis_broker)
    set_broker = MagicMock()
    monkeypatch.setattr(broker_config, "RedisBroker", redis_broker_factory)
    # config.broker re-exports dramatiq; strict mypy forbids implicit re-export reads.
    monkeypatch.setattr(broker_config.dramatiq, "set_broker", set_broker)  # type: ignore[attr-defined]

    configured = broker_config.configure_broker()

    redis_broker_factory.assert_called_once_with(url="redis://queue.example:6380/4")
    set_broker.assert_called_once_with(redis_broker)
    assert configured is redis_broker


@pytest.mark.parametrize("redis_url", [None, "", "   "])
def test_configure_broker_fails_closed_without_a_redis_url(
    monkeypatch: pytest.MonkeyPatch,
    redis_url: str | None,
) -> None:
    if redis_url is None:
        monkeypatch.delenv("REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("REDIS_URL", redis_url)
    redis_broker_factory = MagicMock(name="redis_broker_factory")
    set_broker = MagicMock()
    monkeypatch.setattr(broker_config, "RedisBroker", redis_broker_factory)
    # config.broker re-exports dramatiq; strict mypy forbids implicit re-export reads.
    monkeypatch.setattr(broker_config.dramatiq, "set_broker", set_broker)  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="REDIS_URL is required"):
        broker_config.configure_broker()

    redis_broker_factory.assert_not_called()
    set_broker.assert_not_called()


def test_jobs_package_configures_broker_before_importing_actor() -> None:
    script = """
import sys
from types import ModuleType

import config.broker as broker_config

state = {"configured": False}

def configure_broker():
    state["configured"] = True
    return object()

class GuardedExampleModule(ModuleType):
    def __getattribute__(self, name):
        if name == "process_example" and not state["configured"]:
            raise AssertionError("actor imported before broker configuration")
        return super().__getattribute__(name)

broker_config.configure_broker = configure_broker
example_module = GuardedExampleModule("app.jobs.example")
example_module.process_example = object()
sys.modules["app.jobs.example"] = example_module

import app.jobs

assert state["configured"]
assert app.jobs.process_example is example_module.process_example
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        env=os.environ.copy(),
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_create_app_does_not_import_broker_configuration() -> None:
    script = """
import sys
from importlib.abc import MetaPathFinder

class BrokerImportGuard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "config.broker":
            raise AssertionError("API factory imported worker broker configuration")
        return None

sys.meta_path.insert(0, BrokerImportGuard())

from config.main import create_app

create_app()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        env=os.environ.copy(),
        text=True,
    )

    assert result.returncode == 0, result.stderr
