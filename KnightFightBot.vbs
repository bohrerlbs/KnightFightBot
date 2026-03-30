' KnightFight Bot Launcher
Set objShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strPath
objShell.Run "python launcher.py", 0, False
WScript.Sleep 2000
objShell.Run "http://localhost:8764/launcher", 1, False
