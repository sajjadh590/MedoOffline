$wsh = New-Object -ComObject WScript.Shell
foreach ($name in @('MedoOffline.lnk', 'MedoOffline Web.lnk', 'MedoOffline PDF Book.lnk')) {
    $sc = $wsh.CreateShortcut("E:\Users\Bit\Desktop\$name")
    Write-Host "=== $name ==="
    Write-Host "TargetPath: $($sc.TargetPath)"
    Write-Host "Arguments: $($sc.Arguments)"
    Write-Host "WorkingDirectory: $($sc.WorkingDirectory)"
}
