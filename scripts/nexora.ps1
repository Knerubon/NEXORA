param(
    [Parameter(Mandatory=$true)][ValidateSet('development','production')][string]$Environment,
    [ValidateSet('start','stop','build','describe')][string]$Action = 'start'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Create this worktree virtualenv first (uv sync --extra api).' }
$previous = $env:NEXORA_ENV
try {
    $env:NEXORA_ENV = $Environment
    & $python -m nexora_api.launch $Action
    if ($LASTEXITCODE -ne 0) { throw "NEXORA $Action failed; inspect the error above." }
} finally {
    $env:NEXORA_ENV = $previous
}
