from app.core.db import get_session
from app.main import app


class FakeSession:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        if self.fail:
            raise ConnectionError("connection refused")


def use_session(session: FakeSession) -> None:
    async def override():
        yield session

    app.dependency_overrides[get_session] = override


async def test_health_ok(client):
    session = FakeSession()
    use_session(session)
    res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok"}
    assert session.statements == ["SELECT 1"]


async def test_health_database_down(client):
    use_session(FakeSession(fail=True))
    res = await client.get("/api/health")
    assert res.status_code == 503
    assert res.json() == {"detail": "Không kết nối được cơ sở dữ liệu", "code": "database_unavailable"}
