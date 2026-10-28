import copy

import pytest
from temporalio.testing import WorkflowEnvironment

import activities


@pytest.fixture(autouse=True)
def fast_activities(monkeypatch):
    monkeypatch.setenv("ACTIVITY_DELAY_MIN", "0")
    monkeypatch.setenv("ACTIVITY_DELAY_MAX", "0")


@pytest.fixture(autouse=True)
def fresh_inventory(monkeypatch):
    monkeypatch.setattr(activities, "INVENTORY", copy.deepcopy(activities.INVENTORY))


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_local() as env:
        yield env
