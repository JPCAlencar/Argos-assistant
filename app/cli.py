import argparse
import asyncio
import getpass
import secrets

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import ROOT, Settings
from app.database import Database, Device, User, Workflow
from app.security import hasher
from app.sessions import local_device
from app.workflows import DEFAULT_WORKFLOW


def configure(database_url: str | None = None):
    path = ROOT / ".env"
    if path.exists():
        print(".env já existe; nenhuma configuração sobrescrita.")
        return
    key = Fernet.generate_key().decode()
    url = database_url or input("URL PostgreSQL (postgresql+asyncpg://...): ").strip()
    Settings(database_url=url, master_key=key)
    path.write_text(
        f'ARGOS_DATABASE_URL={url}\nARGOS_MASTER_KEY={key}\nARGOS_ALLOWED_ROOTS=["data/workspace"]\n',
        encoding="utf-8",
    )
    print(".env criado. Preserve a chave mestra e restrinja acesso a este arquivo.")


async def create_admin(username: str, generate: bool, reset: bool = False):
    settings = Settings()
    credential_path = ROOT / ".runtime" / "first-access.txt"
    if generate and credential_path.exists():
        raise ValueError("first-access.txt já existe. Nenhuma credencial sobrescrita.")
    password = (
        secrets.token_urlsafe(24) if generate else getpass.getpass("Senha administrativa (mínimo 12): ")
    )
    if len(password) < 12:
        raise ValueError("Use pelo menos 12 caracteres.")
    if not generate and password != getpass.getpass("Confirme a senha: "):
        raise ValueError("As senhas não coincidem.")
    db = Database(settings.database_url)
    try:
        async with db.sessions() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user and not reset:
                raise ValueError("Usuário já existe; nenhuma senha alterada.")
            if reset and not user:
                raise ValueError("Usuário não encontrado.")
            password_hash = await asyncio.to_thread(hasher.hash, password)
            if reset:
                from sqlalchemy import delete

                from app.database import AdminSession

                user.password_hash = password_hash
                await session.execute(delete(AdminSession).where(AdminSession.user_id == user.id))
            else:
                session.add(User(username=username, password_hash=password_hash))
            await session.commit()
        if generate:
            path = credential_path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(
                    f"ARGOS — primeiro acesso local\nUsuário: {username}\nSenha: {password}\n\n"
                    "Altere a senha em Segurança e remova este arquivo após entrar.\n"
                )
            print("Credencial inicial salva em .runtime/first-access.txt (não versionado).")
        else:
            print("Administrador criado.")
    finally:
        await db.dispose()


async def seed():
    db = Database(Settings().database_url)
    try:
        async with db.sessions() as session:
            if not await session.scalar(select(Workflow.id).where(Workflow.name == DEFAULT_WORKFLOW["name"])):
                session.add(Workflow(**DEFAULT_WORKFLOW))
            if not await session.get(Device, "local"):
                device = await asyncio.to_thread(local_device)
                session.add(Device(id="local", name=device["name"], os=device["os"]))
            await session.commit()
        print("Workflow de diagnóstico e dispositivo local cadastrados.")
    finally:
        await db.dispose()


def main():
    parser = argparse.ArgumentParser(description="Administração local do ARGOS")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-key")
    config = sub.add_parser("configure")
    config.add_argument("--database-url")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--username", default="admin")
    admin.add_argument("--generate", action="store_true")
    reset = sub.add_parser("reset-admin")
    reset.add_argument("--username", default="admin")
    reset.add_argument("--generate", action="store_true")
    sub.add_parser("seed")
    args = parser.parse_args()
    try:
        if args.command == "generate-key":
            print(Fernet.generate_key().decode())
        elif args.command == "configure":
            configure(args.database_url)
        elif args.command in {"create-admin", "reset-admin"}:
            asyncio.run(create_admin(args.username, args.generate, reset=args.command == "reset-admin"))
        elif args.command == "seed":
            asyncio.run(seed())
    except Exception as exc:
        # Não revela URL PostgreSQL com senha contida em mensagens de drivers.
        if isinstance(exc, ValueError):
            raise SystemExit(str(exc)) from None
        raise SystemExit(f"Falha ({type(exc).__name__}). Confira banco, migrações e configuração.") from None


if __name__ == "__main__":
    main()
