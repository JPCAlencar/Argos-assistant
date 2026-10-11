import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import router
from app.config import ROOT, Settings
from app.services import Services
from app.tasks import maintenance


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length", b"0")
        try:
            oversized = int(length) > 150000
        except ValueError:
            oversized = True
        if oversized:
            return await JSONResponse({"detail": "Corpo da requisição excede 150 KB."}, status_code=413)(
                scope, receive, send
            )
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > 150000:
                from starlette.exceptions import HTTPException

                raise HTTPException(413, "Corpo da requisição excede 150 KB.")
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                ]
            await send(message)

        await self.app(scope, bounded_receive, secure_send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx INFO exibe URLs; não registrar chamadas do provedor em nível informativo.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app):
        services = Services(settings)
        app.state.services = services
        lock_connection = None
        try:
            await services.db.check()
            # Lock exclusivo por processo: único worker, recuperação segura após crash.
            lock_connection = await services.db.engine.connect()
            from sqlalchemy import text

            acquired = await lock_connection.scalar(text("SELECT pg_try_advisory_lock(4182701)"))
            await lock_connection.commit()
            if not acquired:
                await lock_connection.close()
                raise RuntimeError("Outro ARGOS usa este banco. Inicie somente um worker.")
            app.state.lock_connection = lock_connection
            for root in settings.allowed_roots:
                root.mkdir(parents=True, exist_ok=True)
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            await services.store.recover()
            janitor = asyncio.create_task(maintenance(services))
            try:
                yield
            finally:
                janitor.cancel()
                await asyncio.gather(janitor, return_exceptions=True)
                await services.tasks.shutdown()
                await lock_connection.execute(text("SELECT pg_advisory_unlock(4182701)"))
                await lock_connection.commit()
                await lock_connection.close()
        finally:
            if lock_connection is not None and not lock_connection.closed:
                await lock_connection.close()
            await services.db.dispose()

    app = FastAPI(
        title="ARGOS", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(SecurityHeaders)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.include_router(router)
    templates = Jinja2Templates(directory=ROOT / "web" / "templates")
    app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"version": "0.1.0"})

    @app.get("/health")
    async def health(request: Request):
        try:
            await request.app.state.services.db.check()
            return {"status": "ok", "version": "0.1.0"}
        except Exception:
            return JSONResponse({"status": "database_unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def errors(request: Request, exc: Exception):
        logging.getLogger("argos").error("Erro de API (%s)", type(exc).__name__)
        return JSONResponse(
            {"detail": "Falha interna. Consulte o serviço e tente novamente."}, status_code=500
        )

    return app


app = create_app()
