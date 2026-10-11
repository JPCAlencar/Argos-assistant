"""PostgreSQL portátil e isolado no workspace, sem serviços ou alterações de sistema."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".runtime"
BIN = RUNTIME / "pgsql" / "bin"
DATA = RUNTIME / "pgdata"
PORT = 55432
URL = "https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip"
SHA256 = "4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(command: list[str]):
    # pg_ctl pode deixar handles herdados no processo servidor. Um arquivo evita
    # esperar pelo fechamento de pipes do servidor que deve continuar executando.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            command, cwd=ROOT, creationflags=FLAGS, stdout=output, stderr=subprocess.STDOUT, timeout=60
        )
        if result.returncode:
            output.seek(0)
            print(output.read().decode("utf-8", errors="replace")[-2000:], file=sys.stderr)
            raise SystemExit("Comando PostgreSQL falhou.")
    return result


def ensure_binaries():
    if (BIN / "postgres.exe").is_file():
        return
    archive = RUNTIME / "postgres.zip"
    if not archive.exists():
        print("Baixando binários oficiais EDB (aproximadamente 326 MB)…")
        urllib.request.urlretrieve(URL, archive)
    with archive.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != SHA256:
        raise SystemExit("Checksum não confere. Arquivo não será executado.")
    with zipfile.ZipFile(archive) as package:
        for member in package.namelist():
            if member.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                target = (RUNTIME / member).resolve()
                if not target.is_relative_to(RUNTIME.resolve()):
                    raise SystemExit("Caminho inválido no arquivo ZIP.")
                package.extract(member, RUNTIME)


def setup():
    import psycopg
    from cryptography.fernet import Fernet
    from psycopg import sql

    RUNTIME.mkdir(exist_ok=True)
    ensure_binaries()
    config_file = RUNTIME / "db-admin.json"
    if not DATA.exists():
        if (ROOT / ".env").exists():
            raise SystemExit(
                ".env existente. Use sua instalação PostgreSQL ou mova a configuração explicitamente."
            )
        password = secrets.token_urlsafe(32)
        with config_file.open("x", encoding="utf-8") as handle:
            json.dump({"password": password}, handle)
        password_file = RUNTIME / "init-password.txt"
        password_file.write_text(password, encoding="utf-8")
        try:
            run(
                [
                    str(BIN / "initdb.exe"),
                    "-D",
                    str(DATA),
                    "-U",
                    "argos_bootstrap",
                    "-E",
                    "UTF8",
                    "--locale=C",
                    "-A",
                    "scram-sha-256",
                    "--pwfile=" + str(password_file),
                ]
            )
        finally:
            password_file.unlink(missing_ok=True)
        with (DATA / "postgresql.conf").open("a", encoding="utf-8") as handle:
            handle.write(
                "\nlisten_addresses = '127.0.0.1'\nport = 55432\n"
                "max_connections = 20\nshared_buffers = '64MB'\n"
            )
    start()
    credentials = json.loads(config_file.read_text(encoding="utf-8"))
    with psycopg.connect(
        host="127.0.0.1",
        port=PORT,
        user="argos_bootstrap",
        password=credentials["password"],
        dbname="postgres",
        autocommit=True,
    ) as connection:
        for dbname, role in [("argos", "argos"), ("argos_test", "argos_test")]:
            exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if not exists:
                password = secrets.token_urlsafe(32)
                connection.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
                connection.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(dbname), sql.Identifier(role)
                    )
                )
                url = f"postgresql+asyncpg://{role}:{password}@127.0.0.1:{PORT}/{dbname}"
                if dbname == "argos":
                    env_path = ROOT / ".env"
                    with env_path.open("x", encoding="utf-8") as handle:
                        handle.write(
                            f"ARGOS_DATABASE_URL={url}\nARGOS_MASTER_KEY={Fernet.generate_key().decode()}\n"
                            'ARGOS_ALLOWED_ROOTS=["data/workspace"]\n'
                        )
                else:
                    (RUNTIME / "test-database-url.txt").write_text(url, encoding="utf-8")
    print("PostgreSQL local pronto em 127.0.0.1:55432. Configuração salva sem expor senhas.")


def start():
    if not DATA.is_dir():
        raise SystemExit("Execute setup primeiro.")
    result = subprocess.run(
        [str(BIN / "pg_ctl.exe"), "status", "-D", str(DATA)], capture_output=True, creationflags=FLAGS
    )
    if result.returncode != 0:
        run([str(BIN / "pg_ctl.exe"), "start", "-D", str(DATA), "-l", str(RUNTIME / "postgres.log"), "-w"])
    print("PostgreSQL portátil iniciado.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "start", "stop"])
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Este auxiliar é específico para Windows. No Linux use PostgreSQL do sistema.")
    if args.action == "setup":
        setup()
    elif args.action == "start":
        start()
    else:
        run([str(BIN / "pg_ctl.exe"), "stop", "-D", str(DATA), "-m", "fast", "-w"])
        print("PostgreSQL local encerrado.")


if __name__ == "__main__":
    main()
