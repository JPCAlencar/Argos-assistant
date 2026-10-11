import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from app.config import ROOT, Settings
from app.database import Base, User
from app.security import hasher
from app.services import Services


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(
        _env_file=None,
        master_key=Fernet.generate_key().decode(),
        allowed_roots=[tmp_path / "workspace"],
        data_dir=tmp_path / "data",
    )


@pytest_asyncio.fixture
async def services(settings):
    url = os.environ.get("ARGOS_TEST_DATABASE_URL")
    local = ROOT / ".runtime" / "test-database-url.txt"
    if not url and local.exists():
        url = local.read_text(encoding="utf-8").strip()
    if not url:
        pytest.skip("Configure ARGOS_TEST_DATABASE_URL para os testes com PostgreSQL real.")
    # Testes nunca apagam tabelas do banco do aplicativo.
    if not url.rsplit("/", 1)[-1].startswith("argos_test"):
        pytest.fail("O banco de testes deve começar com argos_test.")
    settings.database_url = url
    settings.allowed_roots[0].mkdir(parents=True)
    service = Services(settings)
    test_lock = await service.db.engine.connect()
    await test_lock.execute(text("SELECT pg_advisory_lock(4182702)"))
    await test_lock.commit()
    async with service.db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        names = ", ".join('"' + table.name + '"' for table in Base.metadata.sorted_tables)
        await connection.execute(text("TRUNCATE " + names + " RESTART IDENTITY CASCADE"))
    async with service.db.sessions() as session:
        user = User(username="admin", password_hash=await asyncio.to_thread(hasher.hash, "test-password-123"))
        session.add(user)
        await session.commit()
        service.test_user_id = user.id
    try:
        yield service
    finally:
        await service.tasks.shutdown()
        await test_lock.execute(text("SELECT pg_advisory_unlock(4182702)"))
        await test_lock.commit()
        await test_lock.close()
        await service.db.dispose()


async def wait_terminal(services, task_id: str, timeout: float = 5):
    from app.database import Task
    from app.store import TERMINAL

    async with asyncio.timeout(timeout):
        while True:
            async with services.db.sessions() as session:
                task = await session.get(Task, task_id)
            if task.status in TERMINAL and task_id not in services.tasks.running:
                return task
            await asyncio.sleep(0.02)


async def pending_approval(services, task_id: str):
    from app.database import Approval

    async with asyncio.timeout(5):
        while True:
            async with services.db.sessions() as session:
                approval = await session.scalar(
                    select(Approval).where(Approval.task_id == task_id, Approval.status == "PENDING")
                )
            if approval:
                return approval
            await asyncio.sleep(0.02)
