param([Parameter(Position=0)][ValidateSet('setup','build-data','serve','dev-ui','build-ui','test')][string]$Task = 'serve')
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
switch ($Task) {
  'setup'      { python -m venv "$root\.venv"; & $py -m pip install duckdb pandas numpy lifelines fastapi "uvicorn[standard]" pydantic pyyaml httpx pytest ollama; Push-Location "$root\frontend"; npm install; Pop-Location }
  'build-data' { Push-Location "$root\backend"; & $py -m app.pipeline.build; Pop-Location }
  'build-ui'   { Push-Location "$root\frontend"; npm run build; Pop-Location }
  'serve'      { Push-Location "$root\backend"; & $py -m uvicorn app.main:app --port 8000; Pop-Location }
  'dev-ui'     { Push-Location "$root\frontend"; npm run dev; Pop-Location }
  'test'       { Push-Location "$root\backend"; & $py -m pytest -q tests; Pop-Location }
}
