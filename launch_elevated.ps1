param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,

    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,

    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory
)

$resolvedExecutable = [System.IO.Path]::GetFullPath($Executable)
$resolvedScript = [System.IO.Path]::GetFullPath($ScriptPath)
$resolvedWorkingDirectory = [System.IO.Path]::GetFullPath($WorkingDirectory)

if (-not (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf)) {
    throw "ULTRON Python executable not found: $resolvedExecutable"
}
if (-not (Test-Path -LiteralPath $resolvedScript -PathType Leaf)) {
    throw "ULTRON launch script not found: $resolvedScript"
}
if (-not (Test-Path -LiteralPath $resolvedWorkingDirectory -PathType Container)) {
    throw "ULTRON working directory not found: $resolvedWorkingDirectory"
}

$quotedScript = '"' + $resolvedScript.Replace('"', '\"') + '"'

# Try running elevated; if RunAs is not supported or declined, fall back to standard launch
try {
    Start-Process `
        -FilePath $resolvedExecutable `
        -ArgumentList $quotedScript `
        -WorkingDirectory $resolvedWorkingDirectory `
        -Verb RunAs -ErrorAction Stop | Out-Null
} catch {
    try {
        Start-Process `
            -FilePath $resolvedExecutable `
            -ArgumentList $quotedScript `
            -WorkingDirectory $resolvedWorkingDirectory -ErrorAction Stop | Out-Null
    } catch {
        Write-Error "Failed to start ULTRON: $_"
    }
}
