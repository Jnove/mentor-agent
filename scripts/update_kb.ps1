param(
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "Updating knowledge_base submodule..."
git submodule update --init --remote --merge knowledge_base
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Auditing knowledge base..."
& .\.venv\Scripts\python.exe scripts\govern_kb.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$ingestArgs = @("ingest.py")
if ($Rebuild) { $ingestArgs += "--rebuild" }

Write-Host "Updating Chroma index..."
& .\.venv\Scripts\python.exe @ingestArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Knowledge base update complete. Restart Streamlit if it is running."
