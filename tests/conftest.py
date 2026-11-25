import asyncio

import pytest_asyncio


@pytest_asyncio.fixture
async def loop():
    return asyncio.get_running_loop()
