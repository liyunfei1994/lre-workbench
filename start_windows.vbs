Option Explicit
Dim shell, files, root, command, quote
quote = Chr(34)
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
' Check the actual runtime every time, including after a move or code update.
command = "cmd.exe /c " & quote & quote & root & "\start_windows.bat" & quote & quote
shell.Run command, 1, False
