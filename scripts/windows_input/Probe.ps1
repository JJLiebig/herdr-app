#Requires -Version 7.0
param([Parameter(Mandatory)][string] $PlanPath)
. "$PSScriptRoot/Common.ps1"
$plan = Read-GauntletJson $PlanPath
Add-Type -Path "$PSScriptRoot/Native.cs"
$probe = $null
try {
    $probe = [HerdrInputGauntlet.ConsoleProbe]::new($plan.mode)
    [HerdrInputGauntlet.ConsoleProbe]::Print("`r`nHerdr input gauntlet observer $($plan.nonce)`r`n")
    $ack = if ($plan.mode -like 'kitty*') { [Convert]::ToHexString([Text.Encoding]::ASCII.GetBytes("`e[?$($plan.mode.Substring(5))u")).ToLowerInvariant() } else { '' }
    if ($ack) {
        $deadline = [DateTime]::UtcNow.AddSeconds(2)
        while (-not $probe.Hex().Contains($ack) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    }
    $negotiation = $probe.Hex()
    # Publish readiness only after setup replies have been removed from capture.
    $probe.Clear()
    Write-GauntletJson (Join-Path $plan.work 'ready.json') @{
        nonce = $plan.nonce; pid = $PID; mode = $plan.mode; geometry = [HerdrInputGauntlet.ConsoleProbe]::Geometry()
        negotiation_hex = $negotiation; kitty_acknowledged = [bool]($ack -and $negotiation.Contains($ack))
    }
    $closedCount = 0
    $last = ''
    while ((Test-GauntletLease $plan.root) -and -not (Test-Path (Join-Path $plan.work 'probe-stop'))) {
        $request = Read-GauntletJson (Join-Path $plan.work 'request.json')
        if ($null -ne $request -and $request.id -ne $last) {
            if ($request.nonce -ne $plan.nonce) { throw 'Unexpected observer nonce' }
            $last = $request.id
            $quietReached = $false
            if ($request.action -eq 'begin' -and -not $probe.ClearIfCount($closedCount)) { throw 'Unexpected input arrived between captures' }
            if ($request.action -eq 'end') {
                # Capture trailing duplicates/releases as well as the first expected bytes.
                $deadline = [DateTime]::UtcNow.AddSeconds(3)
                $quiet = [DateTime]::UtcNow
                $count = $probe.Count()
                do {
                    Start-Sleep -Milliseconds 50
                    $newCount = $probe.Count()
                    if ($newCount -ne $count) { $quiet = [DateTime]::UtcNow; $count = $newCount }
                } while (([DateTime]::UtcNow - $quiet).TotalMilliseconds -lt 350 -and [DateTime]::UtcNow -lt $deadline)
                $quietReached = ([DateTime]::UtcNow - $quiet).TotalMilliseconds -ge 350
                $closedCount = $probe.Count()
            }
            Write-GauntletJson (Join-Path $plan.work 'reply.json') @{
                id = $request.id; nonce = $plan.nonce; action = $request.action
                hex = $probe.Hex(); records = @($probe.Records()); error = $probe.Error; quiet_reached = $quietReached
                geometry = [HerdrInputGauntlet.ConsoleProbe]::Geometry()
            }
        }
        Start-Sleep -Milliseconds 25
    }
} catch {
    Write-GauntletJson (Join-Path $plan.work 'probe-error.json') @{ error = $_.ToString(); nonce = $plan.nonce }
    throw
} finally {
    if ($null -ne $probe) {
        $probe.Dispose()
        Write-GauntletJson (Join-Path $plan.work 'probe-exit.json') @{ nonce = $plan.nonce; cleanup_errors = @($probe.CleanupErrors) }
    }
}
