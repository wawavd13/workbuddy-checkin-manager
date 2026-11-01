$ErrorActionPreference='Stop'
$desktop=[Environment]::GetFolderPath('Desktop')
$name=[string]::Concat('WorkBuddy ',[char]0x7b7e,[char]0x5230,[char]0x7ba1,[char]0x7406,'.lnk')
$path=Join-Path $desktop $name
if(Test-Path -LiteralPath $path){throw 'Shortcut target already exists; refusing to overwrite.'}
$root=Join-Path $env:USERPROFILE 'Tools\workbuddy-checkin-manager'
$exe=Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
$app=Join-Path $root 'app.py'
if(-not(Test-Path -LiteralPath $exe)-or -not(Test-Path -LiteralPath $app)){throw 'Application runtime is missing.'}
$shell=New-Object -ComObject WScript.Shell
$link=$shell.CreateShortcut($path)
$link.TargetPath=$exe
$link.Arguments='-I "'+$app+'"'
$link.WorkingDirectory=Join-Path $env:SystemRoot 'System32'
$link.Description='WorkBuddy local check-in manager'
$link.IconLocation=Join-Path $env:LOCALAPPDATA 'Programs\WorkBuddy\WorkBuddy.exe,0'
$link.Save()
$check=$shell.CreateShortcut($path)
[pscustomobject]@{Shortcut=$path;Target=$check.TargetPath;Arguments=$check.Arguments;WorkingDirectory=$check.WorkingDirectory}|ConvertTo-Json
