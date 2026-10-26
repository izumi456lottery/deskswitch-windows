' DeskSwitch launcher - no console window
' Finds a suitable pythonw.exe in this order:
'   1) WorkBuddy bundled interpreter (%USERPROFILE%\.workbuddy\binaries\python\versions\*)
'   2) Windows registry (PythonCore / App Paths, HKCU then HKLM)
'   3) Standard install dirs (%LOCALAPPDATA%\Programs\Python\*, C:\Program Files\Python*)
'   4) PATH lookup (where pythonw)
Option Explicit
Dim fso, sh, here, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)

pyw = FindPythonW()
If pyw = "" Then
    MsgBox "DeskSwitch could not find Python (pythonw.exe)." & vbCrLf & vbCrLf & _
           "Install Python 3 from python.org (check 'Add python.exe to PATH'" & vbCrLf & _
           "during setup), then run this launcher again.", _
           vbCritical, "DeskSwitch"
    WScript.Quit 1
End If

sh.CurrentDirectory = here
sh.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & here & "\widget.py" & Chr(34), 0, False


Function FindPythonW()
    Dim p
    ' 1) WorkBuddy bundled interpreters
    p = FirstMatchInDir(sh.ExpandEnvironmentStrings("%USERPROFILE%") & _
                        "\.workbuddy\binaries\python\versions", "pythonw.exe")
    If p <> "" Then FindPythonW = p : Exit Function

    ' 2) Registry: PythonCore InstallPath (try common versions)
    Dim hive, ver
    For Each hive In Array("HKCU", "HKLM")
        For Each ver In Array("3.14", "3.13", "3.12", "3.11", "3.10", "3.9", "3.8")
            p = RegStr(hive & "\SOFTWARE\Python\PythonCore\" & ver & "\InstallPath\")
            If p <> "" Then
                If Right(p, 1) <> "\" Then p = p & "\"
                If fso.FileExists(p & "pythonw.exe") Then
                    FindPythonW = p & "pythonw.exe" : Exit Function
                End If
            End If
        Next
    Next
    ' 2b) App Paths
    p = RegStr("HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\pythonw.exe\")
    If p = "" Then p = RegStr("HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\pythonw.exe\")
    If p <> "" Then
        If fso.FileExists(p) Then FindPythonW = p : Exit Function
    End If

    ' 3) Standard install locations
    p = FirstMatchInDir(sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python", _
                        "pythonw.exe")
    If p = "" Then p = FirstMatchInDir("C:\Program Files", "pythonw.exe")
    If p <> "" Then FindPythonW = p : Exit Function

    ' 4) PATH
    p = FirstLineOfWhere()
    If p <> "" Then FindPythonW = p : Exit Function

    FindPythonW = ""
End Function


Function RegStr(key)
    On Error Resume Next
    RegStr = ""
    Dim v
    v = sh.RegRead(key)
    If Err.Number = 0 Then RegStr = CStr(v)
    On Error Goto 0
End Function


' Search dir (and one level of subdirs) for a file
Function FirstMatchInDir(dirPath, fileName)
    Dim subF, hit
    FirstMatchInDir = ""
    If Not fso.FolderExists(dirPath) Then Exit Function
    hit = fso.BuildPath(dirPath, fileName)
    If fso.FileExists(hit) Then FirstMatchInDir = hit : Exit Function
    For Each subF In fso.GetFolder(dirPath).SubFolders
        hit = fso.BuildPath(subF.Path, fileName)
        If fso.FileExists(hit) Then
            FirstMatchInDir = hit : Exit Function
        End If
    Next
End Function


Function FirstLineOfWhere()
    Dim tmp, ts, line
    FirstLineOfWhere = ""
    tmp = fso.GetSpecialFolder(2) & "\DeskSwitch_pyw.txt"
    sh.Run "cmd /c where pythonw > """ & tmp & """ 2>nul", 0, True
    If fso.FileExists(tmp) Then
        Set ts = fso.OpenTextFile(tmp, 1)
        If Not ts.AtEndOfStream Then
            line = Trim(ts.ReadLine)
            If Len(line) > 0 Then FirstLineOfWhere = line
        End If
        ts.Close
        On Error Resume Next
        fso.DeleteFile tmp, True
        On Error Goto 0
    End If
End Function
