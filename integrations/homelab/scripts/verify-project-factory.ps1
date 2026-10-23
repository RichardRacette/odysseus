# One-shot disposable Windows acceptance drill. Does not change production hub state.
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination
)
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (Get-Command "py" -ErrorAction SilentlyContinue) {
    $exe = "py"
    $prefix = @("-3")
} elseif (Get-Command "python" -ErrorAction SilentlyContinue) {
    $exe = "python"
    $prefix = @()
} else {
    throw "Python 3.10+ is required"
}
function Invoke-CheckedPython([string[]]$Arguments) {
    & $exe @prefix @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Project Factory acceptance step failed (exit $LASTEXITCODE)" }
}
Invoke-CheckedPython @("-m", "unittest", "discover", "-s", "tests", "-v")
Invoke-CheckedPython @("-m", "compileall", "-q", "homelab", "lab.py", "factory.py")
Invoke-CheckedPython @("factory.py", "demo", "--output", $Destination)
Invoke-CheckedPython @("factory.py", "verify-evidence", "--file", (Join-Path $Destination "EVIDENCE.json"))
Write-Output "Local disposable Project Factory acceptance passed; physical-host and GitHub security gates remain separate."
