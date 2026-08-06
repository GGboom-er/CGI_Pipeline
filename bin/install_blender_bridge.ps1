param(
    [string]$BlenderPath = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceDir = Join-Path $ProjectRoot "dccs\blender\extensions\cgi_pipeline_blender_bridge"
$DistDir = Join-Path $ProjectRoot "dccs\blender\extensions\dist"

if (-not $BlenderPath) {
    $envFile = Join-Path $ProjectRoot ".env"
    if (Test-Path -LiteralPath $envFile) {
        $match = Select-String -LiteralPath $envFile -Pattern '^BLENDER_PATH=(.+)$' | Select-Object -First 1
        if ($match) {
            $BlenderPath = $match.Matches[0].Groups[1].Value.Trim()
        }
    }
}
if (-not $BlenderPath) {
    $candidate = Get-ChildItem "C:\Program Files\Blender Foundation" -Recurse -Filter blender.exe -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($candidate) {
        $BlenderPath = $candidate.FullName
    }
}
if (-not (Test-Path -LiteralPath $BlenderPath -PathType Leaf)) {
    throw "Blender executable not found: $BlenderPath"
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
& $BlenderPath --factory-startup --command extension validate $SourceDir
if ($LASTEXITCODE -ne 0) { throw "Blender extension validation failed" }

& $BlenderPath --factory-startup --command extension build --source-dir $SourceDir --output-dir $DistDir
if ($LASTEXITCODE -ne 0) { throw "Blender extension build failed" }

$Package = Get-ChildItem -LiteralPath $DistDir -Filter "cgi_pipeline_blender_bridge-*.zip" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $Package) { throw "Built extension package not found in $DistDir" }

if (-not $SkipInstall) {
    & $BlenderPath --factory-startup --command extension install-file -r user_default -e $Package.FullName
    if ($LASTEXITCODE -ne 0) { throw "Blender extension installation failed" }
}

[pscustomobject]@{
    Status = if ($SkipInstall) { "BUILT" } else { "INSTALLED" }
    Blender = $BlenderPath
    Package = $Package.FullName
}
