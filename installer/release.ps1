<#
  Publish a FaceFrame update. Every installed FaceFrame (1.2.0+) offers it at its next start.

    powershell -ExecutionPolicy Bypass -File installer\release.ps1 -Version 1.2.1 -Notes "What changed"

  1. sets the version (faceframe\__init__.py + installer default), runs the tests
  2. builds dist\FaceFrame-Setup-<ver>.exe
  3. commits, tags v<ver>, pushes
  4. creates the GitHub release with FaceFrame-Setup.exe (the name the app and the
     download link look for), FaceFrame-Setup-<ver>.exe and SHA256SUMS.txt (checked by the app)
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "Bug fixes and improvements.",
    [switch]$SkipTests,
    [switch]$SkipBuild       # publish dist\FaceFrame-Setup-<ver>.exe that was already built and tested
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must look like 1.2.1" }
$py = Join-Path $root ".venv\Scripts\python.exe"
$latest = (gh release view --json tagName --jq .tagName 2>$null)
if (-not $latest) { $latest = "0.0.0" }
$newer = & $py -c "from faceframe.updater import is_newer; print(is_newer('$Version', '$latest'))"
if ($newer.Trim() -ne "True") { throw "Version $Version must be newer than the published $latest (users only get newer versions)" }
if (git status --porcelain) { Write-Host "Uncommitted changes will be included in this release:" -ForegroundColor Yellow; git status --short }

# 1. version + tests
$utf8 = New-Object System.Text.UTF8Encoding($false)      # no BOM
foreach ($f in @(@("faceframe\__init__.py", '__version__ = "[^"]+"', "__version__ = `"$Version`""),
                 @("installer\FaceFrame.iss", '#define AppVersion "[^"]+"', "#define AppVersion `"$Version`""))) {
    $path = Join-Path $root $f[0]
    [IO.File]::WriteAllText($path, ([IO.File]::ReadAllText($path) -replace $f[1], $f[2]), $utf8)
}
if (-not $SkipTests) {
    & $py -m pytest -q -p no:warnings
    if ($LASTEXITCODE -ne 0) { throw "tests failed - nothing was published" }
}

# 2. build
if (-not $SkipBuild) { & powershell -NoProfile -ExecutionPolicy Bypass -File installer\build_installer.ps1 }
$exe = Join-Path $root "dist\FaceFrame-Setup-$Version.exe"
if (-not (Test-Path $exe)) { throw "installer was not built" }

# 3. release files
$rel = Join-Path $root "build\release"
if (Test-Path $rel) { Remove-Item $rel -Recurse -Force }
New-Item -ItemType Directory $rel | Out-Null
Copy-Item $exe (Join-Path $rel "FaceFrame-Setup.exe")
Copy-Item $exe $rel
$lines = Get-ChildItem $rel -Filter *.exe | ForEach-Object {
    "{0} {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name }
[IO.File]::WriteAllLines((Join-Path $rel "SHA256SUMS.txt"), $lines)

# 4. git + GitHub release (the app's update pop-up shows these notes)
git add -A
git commit -m "FaceFrame $Version`n`n$Notes" | Out-Null
git tag "v$Version"
git push origin HEAD --tags
gh release create "v$Version" (Join-Path $rel "FaceFrame-Setup.exe") $exe (Join-Path $rel "SHA256SUMS.txt") `
    --title "FaceFrame $Version" --notes $Notes --latest
Write-Host "Published FaceFrame $Version. Installed copies will offer it at their next start." -ForegroundColor Green
