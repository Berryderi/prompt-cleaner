"""Small always-on-top box for dictating into when the foreground window is a terminal.

Enter = clean and paste, Shift+Enter = newline, Esc = cancel. Win+H dictation types into it
like any text field. Nothing here touches the clipboard or sends Ctrl+C.
"""
from __future__ import annotations

import tkinter as tk

import winapi

HINT = "Dictate here (Win+H).  Enter: clean + paste   Shift+Enter: new line   Esc: cancel"
BOX_WIDTH_CHARS = 72
BOX_HEIGHT_LINES = 8
FOCUS_RETRY_MS = 60


def ask() -> str | None:
    """Show the box modally. Returns the typed text, or None if cancelled/empty."""
    root = tk.Tk()
    root.title("prompt-cleaner")
    root.attributes("-topmost", True)
    result: dict[str, str | None] = {"text": None}

    tk.Label(root, text=HINT, anchor="w", padx=8, pady=4).pack(fill="x")
    box = tk.Text(root, width=BOX_WIDTH_CHARS, height=BOX_HEIGHT_LINES, wrap="word",
                  font=("Segoe UI", 11), padx=8, pady=6, undo=True)
    box.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    def submit(_event=None):
        result["text"] = box.get("1.0", "end-1c")
        root.destroy()
        return "break"

    def cancel(_event=None):
        root.destroy()
        return "break"

    def newline(_event=None):
        box.insert("insert", "\n")
        return "break"

    box.bind("<Return>", submit)
    box.bind("<KP_Enter>", submit)
    box.bind("<Shift-Return>", newline)  # more specific than <Return>, so it wins for Shift+Enter
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")

    def grab_focus():
        # The hotkey press gives this process the right to take the foreground.
        hwnd = root.winfo_id()
        top = winapi.user32.GetParent(hwnd) or hwnd
        winapi.user32.SetForegroundWindow(top)
        root.focus_force()
        box.focus_set()

    root.after(FOCUS_RETRY_MS, grab_focus)
    root.mainloop()
    text = result["text"]
    return text if text and text.strip() else None
