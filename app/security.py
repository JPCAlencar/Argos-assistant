import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from sqlalchemy import delete, select

from app.database import AdminSession, Audit, LoginAttempt, User, now

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))
SECRET_PATTERN = re.compile(r"(?i)(api[_ -]?key|password|senha|secret|token|credential)\s*[:=]\s*\S+")


def redact(value: str) -> str:
    value = SECRET_PATTERN.sub("[segredo omitido]", value)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[segredo omitido]", value)


def redact_data(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    return value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fingerprint(name: str, arguments: dict) -> str:
    return digest(json.dumps({"tool": name, "arguments": arguments}, sort_keys=True, ensure_ascii=False))


class SecretStore:
    def __init__(self, key: str):
        if not key:
            raise RuntimeError("ARGOS_MASTER_KEY ausente. Execute python -m app.cli configure.")
        try:
            self.cipher = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("ARGOS_MASTER_KEY deve ser uma chave Fernet válida.") from exc

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode()


async def audit(db, action: str, data: dict | None = None, user_id: str | None = None):
    async with db.sessions() as session:
        session.add(Audit(action=action, data=data or {}, user_id=user_id))
        await session.commit()


async def authenticate(request: Request) -> AdminSession:
    token = request.cookies.get("argos_session", "")
    if not token:
        raise HTTPException(401, "Entre no painel para continuar.")
    async with request.app.state.services.db.sessions() as session:
        admin = await session.get(AdminSession, digest(token))
        if not admin or admin.expires_at <= now():
            raise HTTPException(401, "Sua sessão expirou. Entre novamente.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), admin.csrf_token):
            raise HTTPException(403, "Token CSRF inválido.")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Origem não autorizada.")
    return admin


async def login(db, settings, username: str, password: str, address: str) -> tuple[str, str]:
    address_hash = digest(address)
    async with db.sessions() as session:
        # Serializa tentativas por origem também quando há requisições concorrentes.
        from sqlalchemy import text

        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(address_hash[:15], 16)})
        await session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now() - timedelta(days=1)))
        attempts = list(
            (
                await session.scalars(
                    select(LoginAttempt).where(
                        LoginAttempt.address_hash == address_hash,
                        LoginAttempt.created_at > now() - timedelta(minutes=15),
                    )
                )
            ).all()
        )
        if len(attempts) >= 5:
            raise HTTPException(429, "Muitas tentativas. Aguarde 15 minutos.")
        user = await session.scalar(select(User).where(User.username == username))
        import asyncio

        def verify():
            try:
                return hasher.verify(user.password_hash if user else DUMMY_HASH, password)
            except (VerificationError, InvalidHashError):
                return False

        valid = await asyncio.to_thread(verify)
        if not valid or not user:
            session.add(LoginAttempt(address_hash=address_hash))
            session.add(Audit(action="login_failed", data={"address_hash": address_hash}))
            await session.commit()
            raise HTTPException(401, "Usuário ou senha inválidos.")
        token, csrf = secrets.token_urlsafe(48), secrets.token_hex(32)
        session.add(
            AdminSession(
                token_hash=digest(token),
                user_id=user.id,
                csrf_token=csrf,
                expires_at=now() + timedelta(hours=settings.session_hours),
            )
        )
        session.add(Audit(user_id=user.id, action="login", data={}))
        await session.commit()
        return token, csrf
