import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import AdminSession, ConfigEntry, Task
from app.main import create_app
from app.providers import ModelMessage, ProviderManager
from tests.conftest import pending_approval, wait_terminal
from tests.test_engine import ScriptedProvider, call

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def client(services):
    app = create_app(services.settings)
    app.state.services = services
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def log_in(client):
    response = await client.post("/api/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


async def test_auth_cookie_csrf_origin_and_rate_limit(client):
    assert (await client.get("/api/tasks")).status_code == 401
    response = await client.get("/")
    assert response.status_code == 200
    assert "ARGOS" in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    await log_in(client)
    csrf = client.headers.pop("X-CSRF-Token")
    assert (await client.post("/api/tasks", json={"objective": "no csrf"})).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert (
        await client.post("/api/logout", headers={"Origin": "https://attacker.example"})
    ).status_code == 403
    for _ in range(5):
        assert (
            await client.post("/api/login", json={"username": "admin", "password": "wrong"})
        ).status_code == 401
    assert (
        await client.post("/api/login", json={"username": "admin", "password": "test-password-123"})
    ).status_code == 429


async def test_provider_config_secret_encrypted_never_returned(client, services):
    await log_in(client)
    assert (
        await client.put("/api/provider", json={"api_key": "sk-this-secret-must-be-encrypted"})
    ).status_code == 200
    response = await client.get("/api/provider")
    assert response.json()["key_configured"]
    assert "sk-this-secret" not in response.text
    assert "encrypted_key" not in response.text
    async with services.db.sessions() as session:
        record = await session.get(ConfigEntry, "provider")
        assert "sk-this-secret" not in str(record.value)
    assert (
        await client.put("/api/provider", json={"base_url": "https://attacker.example"})
    ).status_code == 422


async def test_chat_sse_history_pagination_and_cancel(client, services, monkeypatch):
    await log_in(client)
    monkeypatch.setattr(
        ProviderManager, "create", lambda *_: ScriptedProvider([ModelMessage(content="Olá! Sou ARGOS.")])
    )
    response = await client.post("/api/tasks", json={"objective": "Olá"})
    assert response.status_code == 201
    task = response.json()
    assert (await wait_terminal(services, task["id"])).status == "COMPLETED"
    events = await client.get("/api/tasks/" + task["id"] + "/events")
    assert events.status_code == 200
    assert "event: done" in events.text
    assert "COMPLETED" in events.text
    history = await client.get("/api/conversations/" + task["conversation_id"] + "/messages")
    assert [m["role"] for m in history.json()] == ["user", "assistant"]
    assert (await client.get("/api/tasks?limit=1000")).status_code == 422
    assert (await client.get("/api/tasks/missing")).status_code == 404
    assert (await client.get("/api/tasks?offset=1")).json() == []


async def test_api_approval_and_replay_protection(client, services, monkeypatch):
    await log_in(client)
    monkeypatch.setattr(
        ProviderManager,
        "create",
        lambda *_: ScriptedProvider(
            [
                call("write_file", {"path": "api-approved.txt", "content": "Hello"}),
                ModelMessage(content="Arquivo confirmado."),
            ]
        ),
    )
    response = await client.post("/api/tasks", json={"objective": "Escreva um arquivo"})
    task_id = response.json()["id"]
    approval = await pending_approval(services, task_id)
    assert not (services.settings.allowed_roots[0] / "api-approved.txt").exists()
    assert (await client.post("/api/approvals/" + approval.id, json={"approved": True})).status_code == 200
    final = await wait_terminal(services, task_id)
    assert final.status == "COMPLETED"
    assert (await client.post("/api/approvals/" + approval.id, json={"approved": True})).status_code == 409
    details = (await client.get("/api/tasks/" + task_id)).json()
    assert details["executions"][0]["result"]["confirmed"]


async def test_memory_settings_permissions_password_and_revocation(client, services):
    await log_in(client)
    response = await client.post("/api/memories", json={"key": "editor", "value": "VS Code"})
    assert response.status_code == 201
    memory_id = response.json()["id"]
    assert (
        await client.put("/api/memories/" + memory_id, json={"key": "editor", "value": "Notepad"})
    ).status_code == 200
    assert (
        await client.post("/api/memories", json={"key": "password", "value": "never-store"})
    ).status_code == 422
    assert (await client.delete("/api/memories/" + memory_id)).status_code == 200
    assert (await client.get("/api/memories")).json() == []
    assert (await client.put("/api/tools/write_file", json={"enabled": False})).status_code == 200
    tools = (await client.get("/api/tools")).json()
    assert not next(t for t in tools if t["name"] == "write_file")["enabled"]
    async with services.db.sessions() as session:
        session.add(
            AdminSession(
                token_hash="other-session",
                user_id=services.test_user_id,
                csrf_token="other",
                expires_at=(
                    await session.get(AdminSession, (await session.scalar(select(AdminSession.token_hash))))
                ).expires_at,
            )
        )
        await session.commit()
    assert (await client.post("/api/sessions/revoke")).json()["count"] == 1
    assert (
        await client.put(
            "/api/password", json={"current_password": "wrong", "new_password": "new-test-password-123"}
        )
    ).status_code == 403
    assert (
        await client.put(
            "/api/password",
            json={"current_password": "test-password-123", "new_password": "new-test-password-123"},
        )
    ).status_code == 200


async def test_access_control_for_other_owner_and_body_limit(client, services):
    from app.database import Conversation, User
    from app.security import hasher

    await log_in(client)
    async with services.db.sessions() as session:
        user = User(username="other", password_hash=hasher.hash("other-password-123"))
        session.add(user)
        await session.flush()
        session.add(Conversation(id="other-conversation", user_id=user.id, title="Private"))
        session.add(Task(id="other-task", user_id=user.id, objective="private", status="COMPLETED"))
        await session.commit()
    assert (await client.get("/api/tasks/other-task")).status_code == 404
    assert (await client.post("/api/tasks/other-task/cancel")).status_code == 404
    assert (await client.get("/api/conversations/other-conversation/messages")).status_code == 404
    assert (await client.post("/api/tasks", json={"objective": "x" * 160000})).status_code == 413
    assert (await client.get("/api/screenshots/not-a-real-asset.jpg")).status_code == 404


async def test_lifespan_actual_reopen_database_and_recovery(services):
    async with services.db.sessions() as session:
        session.add(
            Task(id="startup-crash", user_id=services.test_user_id, objective="interrupted", status="RUNNING")
        )
        await session.commit()
    first = create_app(services.settings)
    async with first.router.lifespan_context(first):
        async with first.state.services.db.sessions() as session:
            assert (await session.get(Task, "startup-crash")).status == "FAILED"
        await first.state.services.store.set_config("custom", {"persisted": True})
        second = create_app(services.settings)
        with pytest.raises(RuntimeError, match="Outro ARGOS"):
            async with second.router.lifespan_context(second):
                pass
    reopened = create_app(services.settings)
    async with reopened.router.lifespan_context(reopened):
        assert await reopened.state.services.store.get_config("custom") == {"persisted": True}
