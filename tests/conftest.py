import os

# Tests never touch the real database: set this before the app (and its engine) is imported.
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test?sslmode=disable"
os.environ["FRONTEND_URL"] = "https://tu-truyen-nho.example.app"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
