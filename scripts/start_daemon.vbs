Set WshShell = CreateObject("WScript.Shell")
' 获取当前VBS文件所在目录
strPath = WshShell.CurrentDirectory
Do
    ' 强制在当前目录下执行
    WshShell.Run "cmd.exe /c cd /d """ & strPath & """ && python agent_daemon.py", 0, True
    WScript.Sleep 5000
Loop