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
import tempfile
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path("/Users/tancao/atomic-habit-tracker").resolve()
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "habits_state.json"
CLOUD_META_FILE = DATA_DIR / "cloud_sync.json"
BACKUP_DIR = DATA_DIR / "backups"
BASELINE_FILE = DATA_DIR / "sync_baseline.json"
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


# Serialize local saves so two app requests cannot finish in reverse order.
SAVE_LOCK = threading.Lock()


def push_gist(state_obj: dict):
    """Push synchronously and independently read back the Gist state."""
    payload = {"updatedAt": datetime.now().astimezone().isoformat(), "state": state_obj}
    body = {"description": "Atomic Habit & Streak Tracker — Cloud Sync State",
            "files": {"atomic_habits_state.json": {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
    try:
        result = subprocess.run(
            [GH_BIN, "api", "--method", "PATCH", f"/gists/{DEFAULT_GIST_ID}", "--input", "-"],
            input=json.dumps(body).encode("utf-8"), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode != 0:
            return False, "Không đẩy được Gist"
        verified = pull_gist_state()
        if not verified or verified.get("state") != state_obj:
            return False, "Chưa xác minh được dữ liệu trên Gist"
        return True, ""
    except (OSError, subprocess.TimeoutExpired):
        return False, "Không đẩy được Gist"


def write_state_atomically(path, state):
    """Replace only after a full write; do not expose partial JSON to readers."""
    fd, tmp = tempfile.mkstemp(prefix=".habits-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def pull_gist_state():
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
            # Read local and remote independently. A GET never writes local state.
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else None
            except (OSError, ValueError):
                return self._send_json({"ok": False, "error": "Cannot read local state"}, status=500)
            remote = pull_gist_state()
            if not remote or not isinstance(remote.get("state"), dict):
                cloud_status = "unavailable"
            elif data is None:
                cloud_status = "local_missing"
            elif remote["state"] == data:
                cloud_status = "matches"
            else:
                cloud_status = "conflict"
            return self._send_json({"ok": True, "exists": data is not None,
                                    "state": data, "source": "local_disk",
                                    "cloud_status": cloud_status})

        return super().do_GET()

    def do_POST(self):
        self._handle_save_state()

    def do_PUT(self):
        self._handle_save_state()

    def _handle_save_state(self):
        if not self.path.startswith("/api/state"):
            return self._send_json({"ok": False, "error": "Not found"}, status=404)

        try:
            length = int(self.headers.get("Content-Length", "0"))
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
            state = parsed.get("state", parsed)
            expected = parsed.get("expected_state")
            if "expected_state" not in parsed:
                return self._send_json({"ok": False, "local_saved": False, "error": "Reload required before saving"}, status=409)
            if not isinstance(state, dict) or not isinstance(state.get("habits"), list) or not isinstance(state.get("logs"), dict):
                return self._send_json({"ok": False, "local_saved": False, "error": "Invalid state payload"}, status=400)
        except (ValueError, AttributeError, UnicodeDecodeError):
            return self._send_json({"ok": False, "local_saved": False, "error": "Invalid JSON"}, status=400)

        with SAVE_LOCK:
            try:
                ensure_dirs()
                previous = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else None
                if previous != expected:
                    return self._send_json({"ok": False, "local_saved": False, "error": "Dữ liệu máy đã thay đổi; cửa sổ này cần đối chiếu"}, status=409)
                remote = pull_gist_state()
                baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8")) if BASELINE_FILE.exists() else None
                if remote and isinstance(remote.get("state"), dict) and remote["state"] == previous:
                    baseline = previous
                    write_state_atomically(BASELINE_FILE, baseline)
                # Keep a backup of the state before replacing it.
                if previous is not None:
                    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
                    write_state_atomically(BACKUP_DIR / f"habits_before_save_{stamp}.json", previous)
                write_state_atomically(STATE_FILE, state)
            except (OSError, ValueError):
                return self._send_json({"ok": False, "local_saved": False, "cloud_synced": False,
                                        "error": "Không lưu được dữ liệu trên máy"}, status=500)
            if not remote or not isinstance(remote.get("state"), dict):
                synced, error = False, "Đã lưu trên máy; không đọc được Gist, chưa đồng bộ"
            elif remote["state"] == state:
                synced, error = True, ""
            elif baseline is None or remote["state"] != baseline:
                synced, error = False, "Đã lưu trên máy; dữ liệu Gist khác, chưa đồng bộ"
            else:
                synced, error = push_gist(state)
            if synced:
                try:
                    write_state_atomically(BASELINE_FILE, state)
                except OSError:
                    synced, error = False, "Đã đẩy Gist nhưng không lưu được mốc đồng bộ; cần kiểm tra"
            return self._send_json({"ok": True, "local_saved": True, "cloud_synced": synced,
                                    "cloud_error": error})


if __name__ == "__main__":
    ensure_dirs()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AtomicHabitHandler)
    print(f"Atomic Habit Tracker server running on http://0.0.0.0:{PORT}")
    server.serve_forever()
