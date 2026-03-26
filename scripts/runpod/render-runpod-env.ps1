$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envPath = Join-Path $repoRoot ".env"
$outputPath = Join-Path $repoRoot "runtime\\runpod\\.env.runpod"

if (-not (Test-Path $envPath)) {
    throw "Missing .env at $envPath"
}

$allowedKeys = @(
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "S3_BUCKET",
    "DYNAMODB_PROJECTS_TABLE",
    "DYNAMODB_JOBS_TABLE",
    "DYNAMODB_OUTPUTS_TABLE",
    "DYNAMODB_CONTINUITY_TABLE",
    "WORKER_ID",
    "WORKER_TYPE",
    "WORKER_POLL_INTERVAL_SEC",
    "WORKER_LEASE_SECONDS",
    "WORKER_HEARTBEAT_SECONDS"
)

$values = @{}
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -match '^\s*$') {
        return
    }

    $parts = $_.Split('=', 2)
    if ($parts.Length -ne 2) {
        return
    }

    $values[$parts[0].Trim()] = $parts[1].Trim()
}

New-Item -ItemType Directory -Force -Path (Split-Path $outputPath) | Out-Null

$lines = foreach ($key in $allowedKeys) {
    if ($values.ContainsKey($key)) {
        "$key=$($values[$key])"
    }
}

$lines += "WORKER_ID=runpod-wan-worker"
$lines += "WORKER_TYPE=wan"

Set-Content -Path $outputPath -Value $lines
Write-Output "Wrote $outputPath"
