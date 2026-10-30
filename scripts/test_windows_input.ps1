#Requires -Version 7.0
[CmdletBinding()]
param(
    [string] $AppPath,
    [string] $CorePath = "$env:LOCALAPPDATA/Programs/herdr-core/hc.exe",
    [string] $CoreSource = 'C:/Code/herdr-core',
    [switch] $AllowInputInjection,
    [switch] $MatrixOnly,
    [switch] $ProbeOnly
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot
function Invoke-Preparation($Exe, $Arguments, $Seconds) {
    $info = [Diagnostics.ProcessStartInfo]::new((Get-Command $Exe -ErrorAction Stop).Source)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $repo
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    # An inherited target directory can belong to a different worker.
    $info.Environment['CARGO_TARGET_DIR'] = Join-Path $repo 'target'
    $process = [Diagnostics.Process]::Start($info)
    try {
        if (-not $process.WaitForExit($Seconds * 1000)) { $process.Kill($true); $process.WaitForExit(5000) | Out-Null; throw "$Exe preparation timed out" }
        if ($process.ExitCode) { throw "$Exe preparation failed" }
    } finally { $process.Dispose() }
}
if (-not $IsWindows) { throw 'Windows only' }
if (-not ($MatrixOnly -or $ProbeOnly -or $AllowInputInjection)) { throw 'Read scripts/windows_input/README.md and pass -AllowInputInjection' }
if (-not $AppPath -and -not ($MatrixOnly -or $ProbeOnly)) {
    $lock = 'C:/Code/.worktrees/.rust-build-lock'
    $deadline = [DateTime]::UtcNow.AddMinutes(30)
    while ($true) {
        try { New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null; break }
        catch { if ([DateTime]::UtcNow -ge $deadline) { throw 'Build lock timed out' }; Start-Sleep -Seconds 30 }
    }
    try {
        Set-Content -LiteralPath "$lock/owner" -Value "windows-input-gauntlet PID $PID"
        Push-Location $repo
        try {
            Invoke-Preparation git @('submodule', 'update', '--init', '--depth', '1') 300
            Invoke-Preparation cargo @('build', '--locked') 1800
        }
        finally { Pop-Location }
    } finally { Remove-Item -LiteralPath $lock -Recurse -Force }
    $target = Join-Path $repo 'target'
    $AppPath = Join-Path $target 'debug/herdr-app.exe'
}
$arguments = @("$PSScriptRoot/windows_input/run.py", '--core', $CorePath, '--core-source', $CoreSource)
if ($AppPath) { $arguments += @('--app', $AppPath) }
if ($MatrixOnly) { $arguments += '--matrix-only' }
if ($ProbeOnly) { $arguments += '--probe-only' }
if ($AllowInputInjection) { $arguments += '--allow-input-injection' }
& python @arguments
exit $LASTEXITCODE
