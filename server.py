#!/usr/bin/env python3
"""
Local server for Atomic Habit & Streak Tracker (macOS App + Local Network PWA).
Serves static files and provides /api/state to automatically persist state to
/Users/tancao/atomic-habit-tracker/data/habits_state.json AND mirrors 2-way with
the Secret GitHub Gist so iPhone (over 4G/5G) and MacBook stay in sync 24/7.
"""
import json
import re
import os
import subprocess
import threading
import tempfile
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(os.environ.get("ATOMIC_HABIT_BASE_DIR", str(Path(__file__).resolve().parent))).resolve()
DATA_DIR = Path(os.environ.get("ATOMIC_HABIT_DATA_DIR", str(BASE_DIR / "data"))).resolve()
STATE_FILE = DATA_DIR / "habits_state.json"
PRACTICE_FILE = DATA_DIR / "practice_state.json"
GYM_PLAN_FILE = DATA_DIR / "gym_plan_mock.json"
CLOUD_META_FILE = DATA_DIR / "cloud_sync.json"
BACKUP_DIR = DATA_DIR / "backups"
BASELINE_FILE = DATA_DIR / "sync_baseline.json"
PRACTICE_BASELINE_FILE = DATA_DIR / "practice_sync_baseline.json"
PORT = int(os.environ.get("ATOMIC_HABIT_PORT", "8765"))
PRACTICE_GIST_FILENAME = "atomic_practice_state.json"
DISABLE_GIST_SYNC = os.environ.get("ATOMIC_HABIT_DISABLE_GIST", "0") == "1"
GH_BIN = "/opt/homebrew/bin/gh" if Path("/opt/homebrew/bin/gh").exists() else "gh"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def configured_gist_id():
    """Read the private Gist location from local, ignored configuration."""
    try:
        meta = json.loads(CLOUD_META_FILE.read_text(encoding="utf-8"))
        gist_id = meta.get("gist_id", "")
        if isinstance(gist_id, str) and re.fullmatch(r"[0-9a-fA-F]{32}", gist_id):
            return gist_id
    except (OSError, ValueError, AttributeError):
        pass
    return None


# Serialize local saves so two app requests cannot finish in reverse order.
SAVE_LOCK = threading.Lock()


def push_gist(state_obj: dict):
    """Push synchronously and independently read back the Gist state."""
    gist_id = configured_gist_id()
    if not gist_id or DISABLE_GIST_SYNC:
        return False, "Chưa cấu hình Gist hoặc đồng bộ đang tắt"
    payload = {"updatedAt": datetime.now().astimezone().isoformat(), "state": state_obj}
    body = {"description": "Atomic Habit & Streak Tracker — Cloud Sync State",
            "files": {"atomic_habits_state.json": {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
    try:
        result = subprocess.run(
            [GH_BIN, "api", "--method", "PATCH", f"/gists/{gist_id}", "--input", "-"],
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


def pull_gist_state(filename: str = "atomic_habits_state.json"):
    """Pull latest state from GitHub Secret Gist via gh CLI."""
    gist_id = configured_gist_id()
    if DISABLE_GIST_SYNC or not gist_id:
        return None
    try:
        proc = subprocess.run(
            [GH_BIN, "api", f"/gists/{gist_id}"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        if proc.returncode == 0 and proc.stdout:
            gist_data = json.loads(proc.stdout)
            content = (
                gist_data.get("files", {})
                .get(filename, {})
                .get("content")
            )
            if content:
                parsed = json.loads(content)
                return parsed
    except Exception:
        pass
    return None


def pull_practice_gist_state():
    """Pull latest Practice tab state from its separate file in the Gist."""
    return pull_gist_state(PRACTICE_GIST_FILENAME)


def push_practice_gist(state_obj: dict):
    """Push Practice state synchronously to its separate Gist file on session completion and verify readback."""
    if DISABLE_GIST_SYNC:
        return False, "Đồng bộ Gist đang tắt trong chế độ thử nghiệm"
    gist_id = configured_gist_id()
    if not gist_id:
        return False, "Chưa cấu hình Gist Practice"
    payload = {"updatedAt": datetime.now().astimezone().isoformat(), "state": state_obj}
    body = {
        "description": "Atomic Habit & Streak Tracker — Cloud Sync State",
        "files": {PRACTICE_GIST_FILENAME: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}},
    }
    try:
        result = subprocess.run(
            [GH_BIN, "api", "--method", "PATCH", f"/gists/{gist_id}", "--input", "-"],
            input=json.dumps(body).encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if result.returncode != 0:
            return False, "Không đẩy được Gist Practice"
        verified = pull_practice_gist_state()
        if not verified or verified.get("state") != state_obj:
            return False, "Chưa xác minh được dữ liệu Practice trên Gist"
        return True, ""
    except (OSError, subprocess.TimeoutExpired):
        return False, "Không đẩy được Gist Practice"


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

        if self.path.startswith("/api/practice"):
            try:
                data = json.loads(PRACTICE_FILE.read_text(encoding="utf-8")) if PRACTICE_FILE.exists() else None
                plan = json.loads(GYM_PLAN_FILE.read_text(encoding="utf-8")) if GYM_PLAN_FILE.exists() else None
            except (OSError, ValueError):
                return self._send_json({"ok": False, "error": "Cannot read local practice state"}, status=500)
            remote = pull_practice_gist_state()
            if not remote or not isinstance(remote.get("state"), dict):
                cloud_status = "unavailable"
            elif data is None:
                cloud_status = "local_missing"
            elif remote["state"] == data:
                cloud_status = "matches"
            else:
                cloud_status = "conflict"
            return self._send_json({
                "ok": True,
                "exists": data is not None,
                "state": data,
                "plan": plan,
                "source": "local_disk",
                "cloud_status": cloud_status,
            })

        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/practice"):
            return self._handle_save_practice()
        self._handle_save_state()

    def do_PUT(self):
        if self.path.startswith("/api/practice"):
            return self._handle_save_practice()
        self._handle_save_state()

    def _handle_save_practice(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
            state = parsed.get("state", parsed)
            expected = parsed.get("expected_state")
            push_cloud = bool(parsed.get("push_cloud", False))
            if "expected_state" not in parsed:
                return self._send_json({"ok": False, "local_saved": False, "error": "Reload required before saving practice"}, status=409)
            if (
                not isinstance(state, dict)
                or not isinstance(state.get("meditation_sessions"), list)
                or not isinstance(state.get("gym_sessions"), list)
            ):
                return self._send_json({"ok": False, "local_saved": False, "error": "Invalid practice state payload"}, status=400)
        except (ValueError, AttributeError, UnicodeDecodeError):
            return self._send_json({"ok": False, "local_saved": False, "error": "Invalid JSON"}, status=400)

        with SAVE_LOCK:
            try:
                ensure_dirs()
                previous = json.loads(PRACTICE_FILE.read_text(encoding="utf-8")) if PRACTICE_FILE.exists() else None
                if previous != expected:
                    return self._send_json(
                        {"ok": False, "local_saved": False, "error": "Dữ liệu Thực hành trên máy đã thay đổi; cần tải lại trước khi lưu"},
                        status=409,
                    )
                remote = pull_practice_gist_state() if push_cloud else None
                baseline = json.loads(PRACTICE_BASELINE_FILE.read_text(encoding="utf-8")) if PRACTICE_BASELINE_FILE.exists() else None
                if push_cloud and remote and isinstance(remote.get("state"), dict) and remote["state"] == previous:
                    baseline = previous
                    write_state_atomically(PRACTICE_BASELINE_FILE, baseline)
                if previous is not None:
                    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
                    write_state_atomically(BACKUP_DIR / f"practice_before_save_{stamp}.json", previous)
                write_state_atomically(PRACTICE_FILE, state)
            except (OSError, ValueError):
                return self._send_json(
                    {"ok": False, "local_saved": False, "cloud_synced": False, "error": "Không lưu được dữ liệu Thực hành trên máy"},
                    status=500,
                )

            if not push_cloud:
                return self._send_json({"ok": True, "local_saved": True, "cloud_synced": False, "cloud_skipped": True, "cloud_error": ""})

            if not remote or not isinstance(remote.get("state"), dict):
                synced, error = False, "Đã lưu buổi tập trên máy; không đọc được Gist Practice, chưa đồng bộ"
            elif remote["state"] == state:
                synced, error = True, ""
            elif baseline is not None and remote["state"] != baseline:
                synced, error = False, "Đã lưu buổi tập trên máy; dữ liệu Gist Practice khác, chưa đồng bộ"
            elif previous is not None and baseline is None and remote["state"] != previous:
                synced, error = False, "Đã lưu buổi tập trên máy; dữ liệu Gist Practice khác, chưa đồng bộ"
            else:
                synced, error = push_practice_gist(state)

            if synced:
                try:
                    write_state_atomically(PRACTICE_BASELINE_FILE, state)
                except OSError:
                    synced, error = False, "Đã đẩy Gist Practice nhưng không lưu được mốc đồng bộ; cần kiểm tra"
            return self._send_json({"ok": True, "local_saved": True, "cloud_synced": synced, "cloud_error": error})

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
