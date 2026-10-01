"""macOS Accessibility driving for the Vibe webview.

Every AppleScript body lives here. `entire contents` returns 0 elements for this
process, so each walk is manual recursion from the AXWebArea down.
"""

from __future__ import annotations

import os
from pathlib import Path

from evidence import Blocked, emit, pid_of, require_gate2, run_shell


WINDOW_TITLE = "Vibe"

# The one AX element the doctor resolves to prove the harness itself still works. When a
# lookup fails and this one also fails, the instrument is broken -- not the button.
CONTROL_ELEMENT = "Toggle recents"


def osascript(script: str, args: list[str], run: Path | None, step: str, timeout: float = 90.0) -> dict:
    """Run AppleScript from a temp file so arguments never need shell or AppleScript quoting."""
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"verify-vibe-{os.getpid()}-{step}.applescript"
    tmp.write_text(script, encoding="utf-8")
    try:
        return run_shell(["osascript", str(tmp)] + args, run, step, timeout)
    finally:
        tmp.unlink(missing_ok=True)



# `entire contents` returns 0 elements for this process, so every walk is manual.
_AX_PRELUDE = """
on findRole(el, wantRole, depth, maxd)
	if depth > maxd then return missing value
	tell application "System Events"
		set kids to {}
		try
			set kids to UI elements of el
		end try
		repeat with k in kids
			try
				if role of k is wantRole then return contents of k
			end try
			set hit to my findRole(k, wantRole, depth + 1, maxd)
			if hit is not missing value then return hit
		end repeat
	end tell
	return missing value
end findRole

on webArea(pid)
	tell application "System Events"
		set p to first process whose unix id is pid
		return my findRole(window 1 of p, "AXWebArea", 0, 8)
	end tell
end webArea

on attr(el, attrName)
	set v to ""
	tell application "System Events"
		try
			set v to (value of attribute attrName of el) as text
		end try
	end tell
	return v
end attr

on matches(el, mode, needle)
	set t to my attr(el, "AXTitle")
	if t is "" then set t to my attr(el, "AXDescription")
	if mode is "title" then return t is needle
	if mode is "titlecontains" then return t contains needle
	if mode is "class" then return (my attr(el, "AXDOMClassList")) contains needle
	return false
end matches

on findMatch(el, mode, needle, depth)
	if depth > 14 then return missing value
	tell application "System Events"
		set kids to {}
		try
			set kids to UI elements of el
		end try
		repeat with k in kids
			try
				if my matches(k, mode, needle) then return contents of k
			end try
			set hit to my findMatch(k, mode, needle, depth + 1)
			if hit is not missing value then return hit
		end repeat
	end tell
	return missing value
end findMatch
"""

_PRESS = _AX_PRELUDE + """
on run argv
	set pid to (item 1 of argv) as integer
	set mode to item 2 of argv
	set needle to item 3 of argv
	set wa to my webArea(pid)
	if wa is missing value then return "BLOCKED no AXWebArea"
	set el to my findMatch(wa, mode, needle, 0)
	if el is missing value then return "BLOCKED no element"
	tell application "System Events"
		try
			perform action "AXScrollToVisible" of el
		end try
		perform action "AXPress" of el
	end tell
	return "OK " & (my attr(el, "AXRole")) & " | " & (my attr(el, "AXTitle"))
end run
"""

_EXISTS = _AX_PRELUDE + """
on run argv
	set pid to (item 1 of argv) as integer
	set mode to item 2 of argv
	set needle to item 3 of argv
	set wa to my webArea(pid)
	if wa is missing value then return "BLOCKED no AXWebArea"
	set el to my findMatch(wa, mode, needle, 0)
	if el is missing value then return "ABSENT"
	return "PRESENT " & (my attr(el, "AXRole")) & " | " & (my attr(el, "AXTitle"))
end run
"""

_DUMP = _AX_PRELUDE + """
on walk(el, depth, acc)
	if depth > 14 then return acc
	tell application "System Events"
		set kids to {}
		try
			set kids to UI elements of el
		end try
		repeat with k in kids
			set r to my attr(k, "AXRole")
			set t to my attr(k, "AXTitle")
			set d to my attr(k, "AXDescription")
			set v to my attr(k, "AXValue")
			if (count of v) > 200 then set v to text 1 thru 200 of v
			set c to my attr(k, "AXDOMClassList")
			if (count of c) > 120 then set c to text 1 thru 120 of c
			set acc to acc & depth & tab & r & tab & t & tab & d & tab & v & tab & c & linefeed
			set acc to my walk(k, depth + 1, acc)
		end repeat
	end tell
	return acc
end walk

on run argv
	set pid to (item 1 of argv) as integer
	set wa to my webArea(pid)
	if wa is missing value then return "BLOCKED no AXWebArea"
	return my walk(wa, 0, "")
end run
"""

_OPEN_PANEL = """
on run argv
	set pid to (item 1 of argv) as integer
	set posixPath to item 2 of argv
	tell application "System Events"
		set p to first process whose unix id is pid
		set frontmost of p to true

		-- Guard 1: never type until the panel exists. Keystrokes go to whatever is
		-- frontmost; an ungated keystroke lands in the user's editor.
		set waited to 0
		repeat until (exists (first window of p whose name is not "%WINDOW%"))
			delay 0.25
			set waited to waited + 0.25
			if waited > 15 then return "BLOCKED open panel never appeared"
		end repeat
		set panel to first window of p whose name is not "%WINDOW%"

		keystroke "g" using {command down, shift down}

		-- Guard 2: the Go-to-Folder sheet must exist before the path is typed.
		set waited to 0
		repeat until (exists sheet 1 of panel)
			delay 0.1
			set waited to waited + 0.1
			if waited > 8 then return "BLOCKED go-to-folder sheet never appeared"
		end repeat

		keystroke posixPath
		delay 0.3
		key code 36
		delay 0.8
		key code 36

		set waited to 0
		repeat while (exists (first window of p whose name is not "%WINDOW%"))
			delay 0.25
			set waited to waited + 0.25
			if waited > 15 then return "BLOCKED open panel did not dismiss"
		end repeat
	end tell
	return "OK"
end run
""".replace("%WINDOW%", WINDOW_TITLE)

_QUIT = """
on run argv
	set pid to (item 1 of argv) as integer
	tell application "System Events"
		-- "Quit vibe" stays English even under a zh-TW system locale, which makes it
		-- the one locale-stable native handle in this app.
		click menu item "Quit vibe" of menu 1 of menu bar item "vibe" of menu bar 1 of ¬
			(first process whose unix id is pid)
	end tell
	return "OK"
end run
"""

_AX_TRUSTED = """
on run argv
	tell application "System Events" to return name of first process
end run
"""


# ------------------------------------------------------------------------- subcommands



# ------------------------------------------------------------------- subcommands

def cmd_dump(args) -> None:
    run = Path(args.run).resolve()
    pid = pid_of(run)
    result = osascript(_DUMP, [str(pid)], run, f"dump-{args.step}", timeout=180)
    body = result["stdout"]
    if result["rc"] != 0 or body.startswith("BLOCKED"):
        emit("BLOCKED", f"dump.{args.step}", rc=result["rc"],
             detail=body.strip() or result["stderr"].strip()[:200])
    out = run / "ax" / f"{args.step}.ax.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    emit("PASS", f"dump.{args.step}", rc=0,
         detail=f"nodes={len(body.splitlines())}", source=str(out))


def cmd_press(args) -> None:
    run = Path(args.run).resolve()
    require_gate2(run, "press")
    pid = pid_of(run)
    mode, needle = _selector(args)
    result = osascript(_PRESS, [str(pid), mode, needle], run, f"press-{mode}")
    body = result["stdout"].strip()
    if result["rc"] != 0:
        emit("BLOCKED", "press", rc=result["rc"], detail=result["stderr"].strip()[:200])
    if body.startswith("BLOCKED"):
        # Distinguish "the button is gone" from "the harness lost the web area".
        control = osascript(_EXISTS, [str(pid), "title", CONTROL_ELEMENT], run, "press-control")
        if control["stdout"].strip().startswith("PRESENT"):
            emit("FAIL", "press", rc=0,
                 detail=f"{mode}={needle!r} not found, but control element resolves — "
                        "the element is genuinely absent")
        emit("BLOCKED", "press", rc=0,
             detail=f"{mode}={needle!r} not found AND control element {CONTROL_ELEMENT!r} "
                    "also unresolvable — the instrument is broken, not the app")
    emit("PASS", "press", rc=0, detail=f"{mode}={needle!r} → {body}")


def cmd_exists(args) -> None:
    run = Path(args.run).resolve()
    pid = pid_of(run)
    mode, needle = _selector(args)
    result = osascript(_EXISTS, [str(pid), mode, needle], run, f"exists-{mode}")
    body = result["stdout"].strip()
    if result["rc"] != 0 or body.startswith("BLOCKED"):
        emit("BLOCKED", "exists", rc=result["rc"], detail=body or result["stderr"].strip()[:200])
    if body == "ABSENT":
        control = osascript(_EXISTS, [str(pid), "title", CONTROL_ELEMENT], run, "exists-control")
        if not control["stdout"].strip().startswith("PRESENT"):
            emit("BLOCKED", "exists", rc=0,
                 detail=f"{needle!r} absent and control element also absent — instrument broken")
        emit("FAIL", "exists", rc=0, detail=f"{mode}={needle!r} absent (control resolves)")
    emit("PASS", "exists", rc=0, detail=body)


def _selector(args) -> tuple[str, str]:
    if args.name:
        return "title", args.name
    if args.name_contains:
        return "titlecontains", args.name_contains
    if args.class_contains:
        return "class", args.class_contains
    raise SystemExit("one of --name / --name-contains / --class-contains is required")


def cmd_open_panel(args) -> None:
    run = Path(args.run).resolve()
    require_gate2(run, "open-panel")
    pid = pid_of(run)
    path = Path(args.path).resolve()
    if not path.is_file():
        emit("BLOCKED", "open-panel", rc=1, detail=f"{path} does not exist")
    if not str(path).isascii():
        emit("BLOCKED", "open-panel", rc=1,
             detail=f"{path} is not ASCII; AppleScript `keystroke` cannot type it reliably")
    result = osascript(_OPEN_PANEL, [str(pid), str(path)], run, "open-panel", timeout=120)
    body = result["stdout"].strip()
    if result["rc"] != 0:
        emit("BLOCKED", "open-panel", rc=result["rc"], detail=result["stderr"].strip()[:200])
    if body.startswith("BLOCKED"):
        emit("BLOCKED", "open-panel", rc=0, detail=body)
    emit("PASS", "open-panel", rc=0, detail=f"picked {path}")


