# Adds a Startup shortcut so the backlight idle timer starts at sign-in.
# No console window. Safe to run again: it replaces the same shortcut.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "kbd_idle.py"
if (-not (Test-Path $script)) {
    throw "kbd_idle.py was not found next to this installer."
}

$pythonw = $null
$command = Get-Command pythonw -ErrorAction SilentlyContinue
if ($command) {
    $pythonw = $command.Source
}
if (-not $pythonw) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $candidate = Join-Path (Split-Path $python.Source) "pythonw.exe"
        if (Test-Path $candidate) {
            $pythonw = $candidate
        }
    }
}
if (-not $pythonw) {
    throw "pythonw.exe was not found. Install Python 3.10 or newer and enable 'Add python.exe to PATH'."
}

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "MSI Keyboard Idle.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$script`""
$shortcut.WorkingDirectory = $here
$shortcut.WindowStyle = 7
$shortcut.Description = "Turn the MSI keyboard backlight off when the keyboard is idle"
$shortcut.Save()

Write-Host "Startup shortcut created:"
Write-Host $shortcutPath
Write-Host "It runs at the next sign-in. Delete that shortcut to stop it."
