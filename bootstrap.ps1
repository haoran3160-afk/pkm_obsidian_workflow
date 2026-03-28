param(
    [string]$PythonVersion = "3.12",
    [switch]$RecreateVenv,
    [switch]$SkipTests,
    [switch]$UpgradePip,
    [switch]$ForceInstall,
    [string]$SharedCacheDir = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$GlobalBootstrap = Join-Path (Split-Path -Parent $ProjectRoot) "bootstrap-python.ps1"

if (-not (Test-Path $GlobalBootstrap)) {
    throw "Global bootstrap not found: $GlobalBootstrap"
}

$params = @{
    ProjectPath   = $ProjectRoot
    PythonVersion = $PythonVersion
}

if ($RecreateVenv) {
    $params.RecreateVenv = $true
}

if ($SkipTests) {
    $params.SkipTests = $true
}

if ($UpgradePip) {
    $params.UpgradePip = $true
}

if ($ForceInstall) {
    $params.ForceInstall = $true
}

if (-not [string]::IsNullOrWhiteSpace($SharedCacheDir)) {
    $params.SharedCacheDir = $SharedCacheDir
}

& $GlobalBootstrap @params
