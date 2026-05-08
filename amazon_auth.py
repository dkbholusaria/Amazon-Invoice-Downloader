"""
amazon_auth.py  —  Run ONCE to save an Amazon Business session for automation.

Prerequisites:
    pip install playwright
    playwright install chromium

Double-click this file (or run: python amazon_auth.py)

What it does:
    - Opens Amazon in a browser window
    - You log in normally (including 2FA)
    - Click "Save Session" in the small control window
    - Session is saved to amazon_session.json
    - Re-run whenever Amazon logs you out (typically every 30–60 days)
"""

import asyncio
import os
import threading
import json
import tkinter as tk
from pathlib import Path

# Dedicated per-user directory for all private data
USER_DIR = Path.home() / "amazon_invoice_downloader"
USER_DIR.mkdir(parents=True, exist_ok=True)

# FOR EXE: Force Playwright to use a persistent folder for browsers. 
# Must be set BEFORE importing playwright.
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(USER_DIR / "browsers")

from playwright.async_api import async_playwright

SESSION_FILE = USER_DIR / "amazon_session.json"
RESUME_FILE  = USER_DIR / "resume_job.json"

# ── Shared state between GUI thread and Playwright thread ──────────────────
_save_requested = threading.Event()
_finished       = threading.Event()
_result         = {"ok": False, "msg": ""}

_context_holder = [None]   # [BrowserContext]
_browser_holder = [None]   # [Browser]


# ── Playwright (runs in background thread) ─────────────────────────────────

def _run_browser():
    asyncio.run(_browser_task())


async def _browser_task():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=50,
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        _browser_holder[0] = browser
        _context_holder[0] = context

        page = await context.new_page()
        await page.goto("https://www.amazon.in")

        # Poll until Save is clicked or browser is closed by user
        while not _save_requested.is_set():
            await asyncio.sleep(0.3)
            if not browser.is_connected():
                _result["ok"]  = False
                _result["msg"] = "Browser was closed before saving."
                _finished.set()
                return

        # Save session
        try:
            await context.storage_state(path=str(SESSION_FILE))
            _result["ok"]  = True
            _result["msg"] = f"Saved to:\n{SESSION_FILE}"
        except Exception as exc:
            _result["ok"]  = False
            _result["msg"] = f"Save failed:\n{exc}"

        await browser.close()
        _finished.set()


# ── GUI (runs on main thread) ──────────────────────────────────────────────

class AuthWindow:
    BG        = "#1a1a2e"
    CARD      = "#16213e"
    ACCENT    = "#e8a045"          # Amazon orange-ish
    TEXT      = "#e0e0e0"
    SUBTEXT   = "#888"
    BTN_FG    = "#1a1a2e"
    SUCCESS   = "#4caf50"
    ERROR     = "#f44336"
    W, H      = 420, 320

    def __init__(self, resume_args=None):
        self.resume_args = resume_args
        self.root = tk.Tk()
        self.root.title("Amazon Session Setup")
        self.root.configure(bg=self.BG)
        self.root.resizable(False, False)
        self._centre()
        self.root.attributes("-topmost", True)   # float above the browser

        self._build()

        # Start Playwright in background
        t = threading.Thread(target=_run_browser, daemon=True)
        t.start()

        # Poll for completion
        self.root.after(300, self._poll)
        self.root.mainloop()

    def _centre(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x  = (sw - self.W) // 2
        y  = (sh - self.H) // 2
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def _build(self):
        pad = dict(padx=30)

        # Header bar
        hdr = tk.Frame(self.root, bg=self.ACCENT, height=6)
        hdr.pack(fill="x")

        tk.Label(
            self.root, text="Amazon Session Setup",
            bg=self.BG, fg=self.ACCENT,
            font=("Segoe UI", 15, "bold"),
        ).pack(pady=(22, 4))

        tk.Label(
            self.root,
            text="A browser window has opened.\nLog in to Amazon Business and complete 2FA.",
            bg=self.BG, fg=self.TEXT,
            font=("Segoe UI", 10),
            justify="center",
        ).pack(**pad, pady=(0, 6))

        tk.Label(
            self.root,
            text="Once fully logged in, click the button below.",
            bg=self.BG, fg=self.SUBTEXT,
            font=("Segoe UI", 9),
        ).pack(**pad, pady=(0, 18))

        # Save button
        btn_text = "💾  Save & Resume Download" if self.resume_args else "💾  Save Session"
        self.btn = tk.Button(
            self.root, text=btn_text,
            bg=self.ACCENT, fg=self.BTN_FG,
            font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2",
            padx=24, pady=10,
            command=self._on_save,
            activebackground="#d4922e",
            activeforeground=self.BTN_FG,
        )
        self.btn.pack(pady=(0, 14))

        # Status label
        self.status_var = tk.StringVar(value="Waiting for login…")
        self.status_lbl = tk.Label(
            self.root, textvariable=self.status_var,
            bg=self.BG, fg=self.SUBTEXT,
            font=("Segoe UI", 9),
            wraplength=360, justify="center",
        )
        self.status_lbl.pack(**pad)

        # Footer
        tk.Label(
            self.root, text="Re-run this script whenever Amazon logs you out.",
            bg=self.BG, fg="#555",
            font=("Segoe UI", 8),
        ).pack(side="bottom", pady=10)

    def _on_save(self):
        self.btn.config(state="disabled", text="Saving…", bg="#555", fg=self.TEXT)
        self.status_var.set("Saving session, please wait…")
        self.status_lbl.config(fg=self.TEXT)
        _save_requested.set()

    def _poll(self):
        """Check whether the Playwright thread has finished."""
        if _finished.is_set():
            if _result["ok"]:
                self.btn.config(
                    state="disabled", text="✓  Session Saved",
                    bg=self.SUCCESS, fg="white",
                )
                self.status_var.set(_result["msg"])
                self.status_lbl.config(fg=self.SUCCESS)
                
                if self.resume_args:
                    self._trigger_resume()
                    
                self.root.after(3000, self.root.destroy)   # auto-close after 3 s
            else:
                self.btn.config(
                    state="normal", text="↺  Try Again",
                    bg=self.ERROR, fg="white",
                    command=self._on_retry,
                )
                self.status_var.set(_result["msg"])
                self.status_lbl.config(fg=self.ERROR)
        else:
            self.root.after(300, self._poll)

    def _on_retry(self):
        """Re-open browser after a failure."""
        _save_requested.clear()
        _finished.clear()
        _result["ok"]  = False
        _result["msg"] = ""
        self.btn.config(state="disabled", text="Opening browser…", bg="#555", fg=self.TEXT)
        self.status_var.set("Waiting for login…")
        self.status_lbl.config(fg=self.SUBTEXT)
        t = threading.Thread(target=_run_browser, daemon=True)
        t.start()
        self.root.after(300, self._poll)

    def _trigger_resume(self):
        """Re-launch the main downloader with original arguments."""
        import sys
        import subprocess
        import shlex
        
        try:
            # Prepare the base command (python + script or just EXE)
            if getattr(sys, 'frozen', False):
                full_cmd = [sys.executable]
            else:
                main_script = Path(__file__).parent / "amazon_download_complete_documented.py"
                full_cmd = [sys.executable, str(main_script)]
            
            # Append arguments (handle list from JSON or string from CLI)
            if isinstance(self.resume_args, list):
                full_cmd.extend(self.resume_args)
            elif self.resume_args:
                full_cmd.extend(shlex.split(self.resume_args))
            
            # Launch as a separate process
            subprocess.Popen(full_cmd, cwd=Path(sys.executable).parent)
            
            # Delete the "sticky" resume file
            RESUME_FILE = USER_DIR / "resume_job.json"
            if RESUME_FILE.exists():
                RESUME_FILE.unlink()
        except Exception as exc:
            print(f"Failed to trigger resume: {exc}")


def run_auth(resume_args=None):
    # If no args passed, check for a "sticky" resume file on disk
    if not resume_args and RESUME_FILE.exists():
        try:
            resume_args = json.loads(RESUME_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        AuthWindow(resume_args=resume_args)
        return _result
    except Exception as exc:
        msg = str(exc)
        # Show error in a plain window so it doesn't silently disappear
        import traceback
        root = tk.Tk()
        root.title("amazon_auth — Error")
        root.configure(bg="#1a1a2e")
        root.geometry("520x300")
        msg = traceback.format_exc()
        tk.Label(root, text="Startup error:", bg="#1a1a2e", fg="#f44336",
                 font=("Segoe UI", 11, "bold")).pack(pady=(20, 4))
        txt = tk.Text(root, bg="#0d0d1a", fg="#e0e0e0", font=("Consolas", 9),
                      wrap="word", relief="flat", padx=8, pady=8)
        txt.insert("1.0", msg)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        tk.Label(root, text="Fix the issue above, then re-run.",
                 bg="#1a1a2e", fg="#888", font=("Segoe UI", 9)).pack(pady=(0, 14))
        root.mainloop()
        return {"ok": False, "msg": msg}

if __name__ == "__main__":
    run_auth()
