import pytest
from fastapi import Query
from httpx import ASGITransport, AsyncClient

from app.core.db import to_asyncpg_url
from app.main import create_app


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "postgresql://u:p@ep-x-pooler.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://u:p@ep-x-pooler.aws.neon.tech/neondb?ssl=require",
        ),
        ("postgres://u:p@host/db?sslmode=require", "postgresql+asyncpg://u:p@host/db?ssl=require"),
        ("postgresql+asyncpg://u:p@host/db?ssl=require", "postgresql+asyncpg://u:p@host/db?ssl=require"),
        ("postgresql://u:p@localhost:5432/db?sslmode=disable", "postgresql+asyncpg://u:p@localhost:5432/db"),
        ("postgresql://u:p@localhost/db", "postgresql+asyncpg://u:p@localhost/db"),
    ],
)
def test_to_asyncpg_url(url, expected):
    assert to_asyncpg_url(url) == expected


def test_to_asyncpg_url_rejects_other_databases():
    with pytest.raises(ValueError):
        to_asyncpg_url("mysql://u:p@host/db")


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://localhost:3000", "https://tu-truyen-nho.example.app"])
async def test_cors_allows_frontend_origins(client, origin):
    res = await client.options(
        "/api/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"},
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == origin


async def test_cors_rejects_unknown_origin(client):
    res = await client.options(
        "/api/health", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in res.headers


async def test_unknown_path_uses_error_format(client):
    res = await client.get("/api/nope")
    assert res.status_code == 404
    assert res.json() == {"detail": "Không tìm thấy đường dẫn này", "code": "not_found"}


async def test_validation_error_format():
    test_app = create_app()

    @test_app.get("/api/_echo")
    async def echo(n: int = Query()) -> dict:
        return {"n": n}

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        res = await c.get("/api/_echo", params={"n": "abc"})
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "validation_error"
    assert body["detail"].startswith("Dữ liệu chưa hợp lệ: n:")
