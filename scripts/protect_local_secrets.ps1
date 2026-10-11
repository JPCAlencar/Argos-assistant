$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$ArgosAccount = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (Test-Path -LiteralPath '.runtime') {
    & icacls.exe '.runtime' /inheritance:r /grant:r "${ArgosAccount}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível restringir a pasta .runtime.' }
}
if (Test-Path -LiteralPath '.env') {
    & icacls.exe '.env' /inheritance:r /grant:r "${ArgosAccount}:F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível restringir o arquivo .env.' }
}
Write-Output 'Segredos locais restritos à conta atual; somente arquivos deste workspace foram alterados.'
