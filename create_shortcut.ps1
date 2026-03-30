$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut("$env:USERPROFILE\Desktop\ILUMINATY.lnk")
$s.TargetPath = "$env:USERPROFILE\Desktop\iluminaty\desktop-app\src-tauri\target\release\iluminaty-app.exe"
$s.WorkingDirectory = "$env:USERPROFILE\Desktop\iluminaty"
$s.Description = "ILUMINATY - Eye of God"
$s.Save()
Write-Host "Shortcut created on Desktop"
