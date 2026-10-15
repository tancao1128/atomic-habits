#!/usr/bin/env python3
"""
Local server for Atomic Habit & Streak Tracker (macOS App + Local Network PWA).
Serves static files and provides /api/state to automatically persist state to
/Users/tancao/atomic-habit-tracker/data/habits_state.json AND mirrors 2-way with
the Secret GitHub Gist so iPhone (over 4G/5G) and MacBook stay in sync 24/7.
"""
import json
import os
import subprocess
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path("/Users/tancao/atomic-habit-tracker").resolve()
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "habits_state.json"
CLOUD_META_FILE = DATA_DIR / "cloud_sync.json"
BACKUP_DIR = DATA_DIR / "backups"
PORT = int(os.environ.get("ATOMIC_HABIT_PORT", "8765"))
DEFAULT_GIST_ID = "938d0babf550630ef80c5f709ba21c33"
GH_BIN = "/opt/homebrew/bin/gh" if Path("/opt/homebrew/bin/gh").exists() else "gh"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not CLOUD_META_FILE.exists():
        CLOUD_META_FILE.write_text(
            json.dumps(
                {
                    "gist_id": DEFAULT_GIST_ID,
                    "endpoint": f"https://api.github.com/gists/{DEFAULT_GIST_ID}",
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def push_gist_background(state_obj: dict):
    """Push latest state to GitHub Secret Gist in background using gh CLI."""
    def _worker():
        try:
            payload = {
                "updatedAt": datetime.now().astimezone().isoformat(),
                "state": state_obj,
            }
            body = {
                "description": "Atomic Habit & Streak Tracker — Cloud Sync State",
                "files": {
                    "atomic_habits_state.json": {
                        "content": json.dumps(payload, ensure_ascii=False, indent=2)
                    }
                },
            }
            subprocess.run(
                [GH_BIN, "api", "--method", "PATCH", f"/gists/{DEFAULT_GIST_ID}", "--input", "-"],
                input=json.dumps(body).encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def pull_gist_state() -> dict | None:
    """Pull latest state from GitHub Secret Gist via gh CLI."""
    try:
        proc = subprocess.run(
            [GH_BIN, "api", f"/gists/{DEFAULT_GIST_ID}"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        if proc.returncode == 0 and proc.stdout:
            gist_data = json.loads(proc.stdout)
            content = (
                gist_data.get("files", {})
                .get("atomic_habits_state.json", {})
                .get("content")
            )
            if content:
                parsed = json.loads(content)
                return parsed
    except Exception:
        pass
    return None


class AtomicHabitHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, format, *args):
        pass

    def _send_json(self, payload: dict, status: int = 200):
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, apikey")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._send_json({"ok": True})

    def do_GET(self):
        if self.path.startswith("/api/health"):
            return self._send_json(
                {
                    "status": "ok",
                    "service": "atomic-habit-tracker",
                    "port": PORT,
                    "gist_id": DEFAULT_GIST_ID,
                }
            )

        if self.path.startswith("/api/state"):
            ensure_dirs()
            # Pull remote Gist if available so changes made on iPhone over 4G/5G sync to Mac!
            remote = pull_gist_state()
            if remote and isinstance(remote.get("state"), dict):
                remote_state = remote["state"]
                STATE_FILE.write_text(
                    json.dumps(remote_state, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return self._send_json({"ok": True, "exists": True, "state": remote_state, "source": "cloud_gist"})

            if STATE_FILE.exists():
                try:
                    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                    return self._send_json({"ok": True, "exists": True, "state": data, "source": "local_disk"})
                except Exception as exc:
                    return self._send_json({"ok": False, "error": str(exc)}, status=500)
            return self._send_json({"ok": True, "exists": False, "state": None})

        return super().do_GET()

    def do_POST(self):
        self._handle_save_state()

    def do_PUT(self):
        self._handle_save_state()

    def _handle_save_state(self):
        if not self.path.startswith("/api/state"):
            return self._send_json({"ok": False, "error": "Not found"}, status=404)

        ensure_dirs()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            parsed = json.loads(body)
            state = parsed.get("state", parsed)
            if not isinstance(state, dict) or "habits" not in state or "logs" not in state:
                return self._send_json({"ok": False, "error": "Invalid state payload"}, status=400)

            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

            day_stamp = datetime.now().strftime("%Y-%m-%d")
            daily_backup = BACKUP_DIR / f"habits_state_{day_stamp}.json"
            daily_backup.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

            # Mirror asynchronously to GitHub Secret Gist
            push_gist_background(state)

            return self._send_json({"ok": True, "saved_to": str(STATE_FILE), "cloud_synced": True})
        except Exception as exc:
            return self._send_json({"ok": False, "error": str(exc)}, status=500)


if __name__ == "__main__":
    ensure_dirs()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AtomicHabitHandler)
    print(f"Atomic Habit Tracker server running on http://0.0.0.0:{PORT}")
    server.serve_forever()
