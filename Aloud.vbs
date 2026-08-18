' Starts Aloud with no console window and no taskbar entry.
' Double-click this file, or make a shortcut to it.
Option Explicit

Dim fso, shell, here, pythonw, quoted
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = fso.BuildPath(here, ".venv\Scripts\pythonw.exe")

If Not fso.FileExists(pythonw) Then
  MsgBox "Aloud is not set up yet." & vbCrLf & vbCrLf & _
         "Right-click install.ps1 and choose 'Run with PowerShell', " & _
         "then try again.", vbExclamation, "Aloud"
  WScript.Quit 1
End If

shell.CurrentDirectory = here
quoted = """" & pythonw & """ -m aloud"
' 0 = hidden window, False = do not wait for it to exit.
shell.Run quoted, 0, False
