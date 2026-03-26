$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$distDir = Join-Path $repoRoot "runtime\runpod"
$stageDir = Join-Path $distDir "package"
$zipPath = Join-Path $distDir "text2video-runpod.zip"

if (Test-Path $stageDir) {
    Remove-Item $stageDir -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

$include = @(
    "apps",
    "docs",
    "packages",
    "scripts",
    ".env.example",
    "pyproject.toml",
    "README.md"
)

foreach ($item in $include) {
    $source = Join-Path $repoRoot $item
    $dest = Join-Path $stageDir $item
    if (Test-Path $source) {
        Copy-Item $source $dest -Recurse -Force
    }
}

$runpodEnv = Join-Path $repoRoot "runtime\runpod\.env.runpod"
if (Test-Path $runpodEnv) {
    $envDestDir = Join-Path $stageDir "runtime\runpod"
    New-Item -ItemType Directory -Force -Path $envDestDir | Out-Null
    Copy-Item $runpodEnv (Join-Path $envDestDir ".env.runpod") -Force
}

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $zipPath -Force
Write-Output "Wrote $zipPath"
