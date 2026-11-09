[CmdletBinding()]
param(
    [switch]$ValidateOnly,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$FixtureRoot = $PSScriptRoot
$Checkpoint = Join-Path $FixtureRoot 'NORTHSTAR_STATE.json'
$Guard = Join-Path $FixtureRoot 'src\mission_checkpoint.py'

if (-not (Test-Path -LiteralPath $Guard -PathType Leaf)) {
    throw "NorthStar resume guard is missing: $Guard"
}
if (-not (Test-Path -LiteralPath $Checkpoint -PathType Leaf)) {
    throw "NorthStar checkpoint is missing: $Checkpoint"
}

$Arguments = @($Guard, $Checkpoint)
if ($ValidateOnly) { $Arguments += '--validate-only' }
if ($DryRun) { $Arguments += '--dry-run' }

& python @Arguments
exit $LASTEXITCODE

