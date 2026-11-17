param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    py -3 -m venv .venv
}
& .venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python 라이브러리를 설치하지 못했습니다.' }
if (-not (Test-Path 'static/vendor/build/pdf.mjs')) {
    npm.cmd ci --omit=dev
    if ($LASTEXITCODE -ne 0) { throw 'PDF.js를 설치하지 못했습니다.' }
    npm.cmd run vendor
    if ($LASTEXITCODE -ne 0) { throw 'PDF.js 파일을 준비하지 못했습니다.' }
}
if (-not $env:SOFFICE_PATH) {
    $installedOffice = 'C:/Program Files/LibreOffice/program/soffice.com'
    if (Test-Path $installedOffice) { $env:SOFFICE_PATH = $installedOffice }
    else { Write-Warning 'LibreOffice를 설치하거나 SOFFICE_PATH를 지정해야 PPTX 변환이 가능합니다.' }
}
Write-Host "브라우저에서 http://127.0.0.1:$Port 을 여세요."
if (-not $env:TOTAL_MEMORY_MB) { $env:TOTAL_MEMORY_MB = '768' }
& .venv/Scripts/python.exe -m uvicorn app:app --host 127.0.0.1 --port $Port --workers 1 --no-access-log
