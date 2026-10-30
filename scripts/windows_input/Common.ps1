# Shared test helpers. Dot-sourcing does not touch the desktop or console.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-GauntletJson($Path, $Value) {
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
    for ($attempt = 0; ; $attempt++) {
        try { [IO.File]::Move($temporary, $Path, $true); return }
        catch [IO.IOException], [UnauthorizedAccessException] {
            if ($attempt -ge 99) { throw }
            Start-Sleep -Milliseconds 10
        }
    }
}

function Read-GauntletJson($Path) {
    if (Test-Path -LiteralPath $Path) {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        try {
            $reader = [IO.StreamReader]::new($stream)
            try { return $reader.ReadToEnd() | ConvertFrom-Json -AsHashtable }
            finally { $reader.Dispose() }
        } finally { $stream.Dispose() }
    }
    return $null
}

function Test-GauntletLease($Root) {
    $lease = Get-Item -LiteralPath (Join-Path $Root 'lease') -ErrorAction SilentlyContinue
    return $null -ne $lease -and ([DateTime]::UtcNow - $lease.LastWriteTimeUtc).TotalSeconds -lt 90
}
