Option Explicit
Dim shell, files, root, python, command, quote, marker, markerText, markerStream, ready
quote = Chr(34)
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = root & "\.venv\Scripts\pythonw.exe"
shell.CurrentDirectory = root
ready = False
marker = root & "\.venv\ffsc-runtime.json"
If files.FileExists(marker) Then
    Set markerStream = CreateObject("ADODB.Stream")
    markerStream.Type = 2
    markerStream.Charset = "utf-8"
    markerStream.Open
    markerStream.LoadFromFile marker
    markerText = markerStream.ReadText
    markerStream.Close
    ' Legacy interface-only markers must go through the complete setup check.
    ready = (InStr(markerText, """fingerprint""") > 0) And (InStr(markerText, """functional_checks""") > 0)
End If
' Use the visible installer until setup has successfully completed.
If files.FileExists(python) And ready Then
    command = quote & python & quote & " " & quote & root & "\run_app.py" & quote
    shell.Run command, 0, False
Else
    command = "cmd.exe /c " & quote & quote & root & "\start_windows.bat" & quote & quote
    shell.Run command, 1, False
End If
