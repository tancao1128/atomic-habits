import contextlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('atomic_server', Path(__file__).resolve().parents[1] / 'server.py')
srv = importlib.util.module_from_spec(spec);spec.loader.exec_module(srv)

class ReadStateTests(unittest.TestCase):
    def test_get_does_not_overwrite_conflicting_local(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fake-state.json';local={'habits':[{'id':'local'}], 'logs':{}}
            path.write_text(json.dumps(local));before=path.read_bytes()
            remote={'state':{'habits':[{'id':'remote'}], 'logs':{}}}
            with patch.object(srv,'STATE_FILE',path), patch.object(srv,'pull_gist_state',return_value=remote):
                http=srv.ThreadingHTTPServer(('127.0.0.1',0),srv.AtomicHabitHandler)
                thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
                try:
                    with urlopen('http://127.0.0.1:%d/api/state'%http.server_port,timeout=3) as response: data=json.load(response)
                finally:http.shutdown();http.server_close();thread.join()
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(data['state'],local);self.assertEqual(data['cloud_status'],'conflict')
