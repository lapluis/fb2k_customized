[CmdletBinding()]
param(
    [string]$TargetDirectory = (Join-Path $PSScriptRoot 'artifact\foobar2000'),
    [string]$OutputDirectory,
    [ValidateSet('Fixed', 'Ask')]
    [string]$OutputMode = 'Fixed',
    [string]$CacheDirectory = (Join-Path $PSScriptRoot 'cache'),
    [string]$QaacDirectory = 'C:\Tools\OKEGui\tools\qaac',
    [switch]$Offline,
    [switch]$DownloadOnly,
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) { throw 'Python 3.10 or newer is required; add python.exe to PATH.' }
& $pythonCommand.Source -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required.' }
$buildArguments = @((Join-Path $PSScriptRoot 'build.py'), '--target', $TargetDirectory, '--cache', $CacheDirectory)
$buildArguments += @('--qaac-source', $QaacDirectory)
$buildArguments += @('--output-mode', $OutputMode.ToLowerInvariant())
if ($OutputDirectory) { $buildArguments += @('--output', $OutputDirectory) }
if ($Offline) { $buildArguments += '--offline' }
if ($DownloadOnly) { $buildArguments += '--download-only' }
if ($VerifyOnly) { $buildArguments += '--verify-only' }
& $pythonCommand.Source @buildArguments
if ($LASTEXITCODE -ne 0) { throw "Build failed (exit code $LASTEXITCODE)." }
