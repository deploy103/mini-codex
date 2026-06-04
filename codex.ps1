param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CodexArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Show-EasyHelp {
    @"
Easy commands:
  ./codex.ps1 "task"          Run the local coding agent
  ./codex.ps1 gui             Open the desktop app window
  ./codex.ps1 config          Show resolved non-secret config
  ./codex.ps1 doctor          Check local setup
  ./codex.ps1 dry "task"      Preview the plan without writing files or running commands
  ./codex.ps1 permission list Manage shell-command permission profiles
  ./codex.ps1 test            Run pytest
  ./codex.ps1 logs            List recent run transcripts
  ./codex.ps1 last            Print the latest run transcript
  ./codex.ps1 status          Show workspace, git, transcript, and permission status
  ./codex.ps1 diff            Show staged and unstaged git diff stats
"@ | Write-Host

    $localVenvPy = Join-Path $Root ".venv-win\Scripts\python.exe"
    if (Test-Path $localVenvPy) {
        Write-Host ""
        Write-Host "mini-codex options:"
        & $localVenvPy -m mini_codex --help
    } else {
        Write-Host ""
        Write-Host 'Run ./codex.ps1 config or ./codex.ps1 "task" once to initialize the local environment.'
    }
}

function Show-Logs {
    $runDir = Join-Path $Root ".mini_codex\runs"
    if (Test-Path $runDir) {
        $items = Get-ChildItem $runDir -Filter "*.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 20
        if ($items) {
            $items | Select-Object -ExpandProperty FullName
        } else {
            Write-Host "No run transcripts yet."
        }
    } else {
        Write-Host "No run transcripts yet."
    }
}

function Show-Last {
    $runDir = Join-Path $Root ".mini_codex\runs"
    if (-not (Test-Path $runDir)) {
        Write-Host "No run transcripts yet."
        return 1
    }
    $latest = Get-ChildItem $runDir -Filter "*.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) {
        Write-Host "No run transcripts yet."
        return 1
    }
    Get-Content $latest.FullName -Raw
    return 0
}

$first = if ($CodexArgs.Count -gt 0) { $CodexArgs[0] } else { "" }
$rest = if ($CodexArgs.Count -gt 1) { $CodexArgs[1..($CodexArgs.Count - 1)] } else { @() }

switch ($first) {
    { $_ -in @("help", "-h", "--help") } {
        Show-EasyHelp
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
        exit 0
    }
    "logs" {
        if ($rest.Count -eq 0) {
            Show-Logs
            exit 0
        }
    }
    "last" {
        if ($rest.Count -eq 0) {
            $code = Show-Last
            exit $code
        }
    }
}

function Invoke-BasePython {
    param([string[]] $PythonArgs)

    if ($env:PYTHON) {
        & $env:PYTHON @PythonArgs
        return $LASTEXITCODE
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3 @PythonArgs
        return $LASTEXITCODE
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & python @PythonArgs
        return $LASTEXITCODE
    }

    Write-Error "python, py, or PYTHON is required."
    return 1
}

$VenvDir = Join-Path $Root ".venv-win"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "[setup] Creating .venv-win"
    Write-Host "[setup] command: python -m venv .venv-win"
    $code = Invoke-BasePython @("-m", "venv", ".venv-win")
    if ($code -ne 0) { exit $code }
}

function Ensure-Pip {
    & $VenvPy -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[setup] Bootstrapping pip"
        Write-Host "[setup] command: $VenvPy -m ensurepip --upgrade"
        & $VenvPy -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

$ReadyMarker = Join-Path $VenvDir ".mini_codex_ready"
$NeedsSetup = -not (Test-Path $ReadyMarker)
if (-not $NeedsSetup) {
    $marker = Get-Item $ReadyMarker
    foreach ($path in @("pyproject.toml", "requirements.txt")) {
        if ((Test-Path $path) -and ((Get-Item $path).LastWriteTime -gt $marker.LastWriteTime)) {
            $NeedsSetup = $true
        }
    }
}

if ($NeedsSetup) {
    $check = @"
import importlib.metadata
import dotenv
import openai
import rich

importlib.metadata.version("mini-codex")
"@
    & $VenvPy -c $check *> $null
    if ($LASTEXITCODE -ne 0) {
        Ensure-Pip
        Write-Host "[setup] Installing mini-codex and dependencies"
        Write-Host "[setup] command: $VenvPy -m pip install -e .[dev]"
        & $VenvPy -m pip install -e ".[dev]"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    New-Item -ItemType File -Force $ReadyMarker | Out-Null
}

switch ($first) {
    "config" {
        $CodexArgs = @("--show-config") + $rest
    }
    "doctor" {
        $CodexArgs = @("--doctor") + $rest
    }
    "dry" {
        $CodexArgs = @("--dry-run", "--no-commands") + $rest
    }
    "gui" {
        $CodexArgs = @("--gui") + $rest
    }
    "test" {
        & $VenvPy -m pytest -q @rest
        exit $LASTEXITCODE
    }
}

& $VenvPy -m mini_codex @CodexArgs
exit $LASTEXITCODE
