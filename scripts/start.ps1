param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Crie o ambiente virtual conforme o README.' }
if (Test-Path -LiteralPath '.runtime\pgdata\PG_VERSION') {
    & $Python scripts/local_postgres.py start
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao iniciar PostgreSQL local.' }
}
& $Python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Falha ao aplicar migrações.' }
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1
