"""One-command kill switch: `python kill_switch.py on` stops ALL new live buys
(sells still work so you can exit); `off` re-enables; `status` shows it."""
import os
import sys

FILE = "KILL_SWITCH"
cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
if cmd == "on":
    open(FILE, "w").write("on\n")
    print("KILL SWITCH ON -- no new live buys")
elif cmd == "off":
    if os.path.exists(FILE):
        os.remove(FILE)
    print("kill switch off")
else:
    print("ON" if os.path.exists(FILE) else "off")
