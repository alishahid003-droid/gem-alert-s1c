Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\Users\razas\Downloads\gem-alert-s1c_6 && python scheduler.py --poll-madeonsol", 0, False
