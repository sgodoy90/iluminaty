# restart_server.ps1 — kill ONLY the HTTP server process (port 8420), not MCP stdio
# Usage: .\scripts\restart_server.ps1
$port = 8420
$conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $pid = $conn.OwningProcess
    Write-Host "Killing server PID $pid on :$port"
    Stop-Process -Id $pid -Force
    Start-Sleep -Seconds 2
} else {
    Write-Host "No process on :$port"
}
Write-Host "Server stopped. Start with: bg_shell start ..."
