' DeskSwitch launcher - no console window
Option Explicit
Dim fso, sh, here, pyw, tmp, ts, line
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)

' 自动在 PATH 里找 pythonw.exe（找不到就退化成裸命令，让系统自己解析）
pyw = ""
tmp = fso.GetSpecialFolder(2) & "\DeskSwitch_pyw.txt"
sh.Run "cmd /c where pythonw > """ & tmp & """ 2>nul", 0, True
If fso.FileExists(tmp) Then
    Set ts = fso.OpenTextFile(tmp, 1)
    If Not ts.AtEndOfStream Then
        line = Trim(ts.ReadLine)
        If Len(line) > 0 Then pyw = line
    End If
    ts.Close
    On Error Resume Next
    fso.DeleteFile tmp, True
    On Error Goto 0
End If
If pyw = "" Then pyw = "pythonw.exe"

sh.CurrentDirectory = here
sh.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & here & "\widget.py" & Chr(34), 0, False
