$desktops = @('E:\Users\Bit\Desktop', 'C:\Users\Bit\Desktop')
$wsh = New-Object -ComObject WScript.Shell

$root = "E:\MedoOffline"
$pythonw = "E:\MedoOffline\.venv\Scripts\pythonw.exe"
$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$webPath = "E:\MedoOffline\dist\index.html"
$iconPath = "E:\MedoOffline\medo\web\icons\medo.ico"
$pdfPath = $null
foreach ($candidate in @("E:\MedoOffline\dist\medo_test_book.pdf", "E:\MedoOffline\dist\test_book.pdf")) {
    if (Test-Path $candidate) { $pdfPath = $candidate; break }
}

foreach ($d in $desktops) {
    if (-not (Test-Path $d)) { continue }

    # 1) Main app: local server + auto-open browser. THIS is the one to use —
    #    extraction ("دانلود سوالات") only works when the server is running.
    $sc1 = $wsh.CreateShortcut("$d\MedoOffline.lnk")
    $sc1.TargetPath = $pythonw
    $sc1.Arguments = "launcher.py"
    $sc1.WorkingDirectory = $root
    if (Test-Path $iconPath) { $sc1.IconLocation = "$iconPath,0" }
    $sc1.Description = "MedoOffline - Practice + Question Extraction (local server)"
    $sc1.Save()

    # 2) Static offline viewer (file://) - study only, no extraction
    $sc2 = $wsh.CreateShortcut("$d\MedoOffline Viewer.lnk")
    if (Test-Path $chromePath) {
        $sc2.TargetPath = $chromePath
        $sc2.Arguments = "`"$webPath`""
        $sc2.IconLocation = "$chromePath,0"
    } else {
        $sc2.TargetPath = "explorer.exe"
        $sc2.Arguments = "`"$webPath`""
    }
    $sc2.WorkingDirectory = "$root\dist"
    $sc2.Description = "MedoOffline - Offline viewer (no server, extraction disabled)"
    $sc2.Save()

    # 3) Printable PDF book (only if it exists)
    if ($pdfPath) {
        $sc3 = $wsh.CreateShortcut("$d\MedoOffline PDF Book.lnk")
        $sc3.TargetPath = "explorer.exe"
        $sc3.Arguments = "`"$pdfPath`""
        $sc3.WorkingDirectory = "$root\dist"
        if (Test-Path $iconPath) { $sc3.IconLocation = "$iconPath,0" }
        $sc3.Description = "MedoOffline - Printable question book (PDF)"
        $sc3.Save()
    }

    # 4) CLI console
    $sc4 = $wsh.CreateShortcut("$d\MedoOffline CLI.lnk")
    $sc4.TargetPath = "cmd.exe"
    $sc4.Arguments = "/k cd /d `"$root`""
    $sc4.WorkingDirectory = $root
    $sc4.Description = "MedoOffline - Command Line Tools"
    $sc4.Save()

    Write-Host "Configured shortcuts in: $d"
}
