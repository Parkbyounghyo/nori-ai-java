$javac = "C:\eGovFrameDev-3.10.0-64bit\UTIL\jdk1.8.0_331\bin\javac.exe"
$jar = "C:\eGovFrameDev-3.10.0-64bit\UTIL\jdk1.8.0_331\bin\jar.exe"
$pluginDir = "c:\nori-ai-java\nori-plugin"
$srcDir = "$pluginDir\src"
$binDir = "$pluginDir\bin"
$eclipsePlugins = "C:\eGovFrameDev-3.10.0-64bit\eclipse\plugins"

if (Test-Path $binDir) { Remove-Item -Recurse -Force $binDir }
New-Item -ItemType Directory -Path $binDir -Force | Out-Null

$cpJars = @()
$patterns = @("org.eclipse.swt.win32.win32.x86_64_*.jar","org.eclipse.jface_*.jar","org.eclipse.core.runtime_*.jar","org.eclipse.core.resources_*.jar","org.eclipse.core.jobs_*.jar","org.eclipse.core.commands_*.jar","org.eclipse.equinox.common_*.jar","org.eclipse.equinox.registry_*.jar","org.eclipse.osgi_*.jar","org.eclipse.ui.workbench_*.jar","org.eclipse.ui.ide_*.jar","org.eclipse.jface.text_*.jar","org.eclipse.ui.editors_*.jar","org.eclipse.text_*.jar","org.eclipse.ui.workbench.texteditor_*.jar","org.eclipse.ui.console_*.jar","org.eclipse.debug.ui_*.jar")
foreach ($p in $patterns) { $f = Get-ChildItem "$eclipsePlugins\$p" -EA SilentlyContinue | Select-Object -First 1; if ($f) { $cpJars += $f.FullName } }
$uiDir = Get-ChildItem "$eclipsePlugins" -Directory -Filter "org.eclipse.ui_*" | Select-Object -First 1
if ($uiDir) { $uiJar = Get-ChildItem $uiDir.FullName -Filter "*.jar" -Recurse | Select-Object -First 1; if ($uiJar) { $cpJars += $uiJar.FullName } }
$cp = $cpJars -join ";"

$srcFiles = Get-ChildItem -Path $srcDir -Filter "*.java" -Recurse | ForEach-Object { $_.FullName }
$srcListFile = "$binDir\sources.txt"
$srcFiles | Out-File -FilePath $srcListFile -Encoding ascii

Write-Host "Compiling..."
$err = & $javac -source 1.8 -target 1.8 -encoding UTF-8 -d $binDir -cp $cp "@$srcListFile" 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host "COMPILE FAILED"; $err; exit 1 }
Write-Host "Compile OK"

Copy-Item "$pluginDir\plugin.xml" "$binDir\plugin.xml" -Force
Remove-Item "$binDir\sources.txt" -Force -EA SilentlyContinue

# highlight.js 리소스를 JAR에 포함
if (Test-Path "$pluginDir\resources") {
    Copy-Item "$pluginDir\resources" "$binDir\resources" -Recurse -Force
}

$jarFile = "$binDir\nori.ai.plugin_1.0.0.jar"
Push-Location $binDir
& $jar cfm $jarFile "$pluginDir\META-INF\MANIFEST.MF" .
Pop-Location

$dest = "C:\eGovFrameDev-3.10.0-64bit\eclipse\dropins\nori.ai.plugin_1.0.0.jar"
[System.IO.File]::Copy($jarFile, $dest, $true)
$f = Get-Item $dest
Write-Host "DEPLOYED: $($f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')) $($f.Length)b ($([math]::Round($f.Length/1KB,1))KB)"
