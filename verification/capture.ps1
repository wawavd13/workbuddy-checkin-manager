param([Parameter(Mandatory=$true)][string]$OutputPath,[int]$Left=0,[int]$Top=0,[int]$Width=1240,[int]$Height=860)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System.Runtime.InteropServices;
public static class ScreenDpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
'@
[ScreenDpi]::SetProcessDPIAware() | Out-Null
$bitmap=New-Object System.Drawing.Bitmap($Width,$Height)
$graphics=[System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($Left,$Top,0,0,$bitmap.Size)
    $bitmap.Save($OutputPath,[System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
