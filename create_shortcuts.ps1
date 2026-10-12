$desktops = @('E:\Users\Bit\Desktop', 'C:\Users\Bit\Desktop')
$wsh = New-Object -ComObject WScript.Shell

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$webPath = "E:\MedoOffline\dist\index.html"
$pdfPath = "E:\MedoOffline\dist\test_book.pdf"
$batPath = "E:\MedoOffline\launch_medo.bat"

foreach ($d in $desktops) {
    if (Test-Path $d) {
        # Shortcut 1: Main Web App - launches instantly in Chrome
        $sc1 = $wsh.CreateShortcut("$d\MedoOffline.lnk")
        if (Test-Path $chromePath) {
            $sc1.TargetPath = $chromePath
            $sc1.Arguments = "`"$webPath`""
            $sc1.IconLocation = "$chromePath,0"
        } else {
            $sc1.TargetPath = "explorer.exe"
            $sc1.Arguments = "`"$webPath`""
        }
        $sc1.WorkingDirectory = "E:\MedoOffline\dist"
        $sc1.Description = "MedoOffline - Interactive Medical MCQ App"
        $sc1.Save()

        # Shortcut 2: Printable PDF Book
        $sc2 = $wsh.CreateShortcut("$d\MedoOffline PDF Book.lnk")
        $sc2.TargetPath = "explorer.exe"
        $sc2.Arguments = "`"$pdfPath`""
        $sc2.WorkingDirectory = "E:\MedoOffline\dist"
        $sc2.Description = "MedoOffline - Medical Test Book PDF"
        $sc2.Save()

        # Shortcut 3: CLI & Scraper Launcher
        $sc3 = $wsh.CreateShortcut("$d\MedoOffline CLI.lnk")
        $sc3.TargetPath = "cmd.exe"
        $sc3.Arguments = "/k cd /d E:\MedoOffline"
        $sc3.WorkingDirectory = "E:\MedoOffline"
        $sc3.Description = "MedoOffline - Command Line Tools"
        $sc3.Save()

        Write-Host "Configured shortcuts in: $d"
    }
}
