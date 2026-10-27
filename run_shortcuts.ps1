# One-time: create shortcuts, remove stale ones, print what landed on the desktop
.\create_shortcuts.ps1

foreach ($d in @('E:\Users\Bit\Desktop', 'C:\Users\Bit\Desktop')) {
    $stale = Join-Path $d 'MedoOffline Web.lnk'
    if (Test-Path $stale) { Remove-Item $stale -Force; Write-Host "Removed stale: $stale" }
}

$wsh = New-Object -ComObject WScript.Shell
foreach ($d in @('E:\Users\Bit\Desktop', 'C:\Users\Bit\Desktop')) {
    if (-not (Test-Path $d)) { continue }
    Get-ChildItem "$d\MedoOffline*.lnk" | ForEach-Object {
        $sc = $wsh.CreateShortcut($_.FullName)
        Write-Host ("{0}  ==>  {1} {2}" -f $_.Name, $sc.TargetPath, $sc.Arguments)
    }
}
