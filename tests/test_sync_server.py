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

class SaveStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'state.json';self.backups=self.root/'backups'
        self.old={'habits':[{'id':'fake'}],'logs':{}};self.new={'habits':[{'id':'fake'}],'logs':{'2026-11-01':{'fake':True}}}
        self.path.write_text(json.dumps(self.old));self.before=self.path.read_bytes()
        for k,v in [('STATE_FILE',self.path),('DATA_DIR',self.root),('BACKUP_DIR',self.backups),('CLOUD_META_FILE',self.root/'meta.json')]:
            patcher=patch.object(srv,k,v);patcher.start();self.addCleanup(patcher.stop)
    def post(self):
        from urllib.request import Request
        from urllib.error import HTTPError
        server=srv.ThreadingHTTPServer(('127.0.0.1',0),srv.AtomicHabitHandler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            req=Request('http://127.0.0.1:%d/api/state'%server.server_port,data=json.dumps({'state':self.new}).encode(),method='POST',headers={'Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=5) as r:return r.status,json.load(r)
            except HTTPError as e:return e.code,json.load(e)
        finally:server.shutdown();server.server_close();t.join()
    def test_failed_cloud_is_reported_but_local_saved(self):
        with patch.object(srv,'pull_gist_state',return_value={'state':self.old}),patch.object(srv,'push_gist',return_value=(False,'fake failure')):
            code,result=self.post()
        self.assertEqual(code,200);self.assertTrue(result['local_saved']);self.assertFalse(result['cloud_synced']);self.assertIn('failure',result['cloud_error'])
        self.assertEqual(json.loads(self.path.read_text()),self.new)
        self.assertEqual(json.loads(next(self.backups.glob('habits_before_save_*')).read_text()),self.old)
    def test_cloud_conflict_prevents_push(self):
        with patch.object(srv,'pull_gist_state',return_value={'state':{'habits':[],'logs':{}}}),patch.object(srv,'push_gist') as push:
            _,result=self.post()
        self.assertTrue(result['local_saved']);self.assertFalse(result['cloud_synced']);push.assert_not_called()
    def test_local_failure_never_claims_saved(self):
        with patch.object(srv,'pull_gist_state',return_value={'state':self.old}),patch.object(srv,'write_state_atomically',side_effect=OSError('fake')),patch.object(srv,'push_gist') as push:
            code,result=self.post()
        self.assertEqual(code,500);self.assertFalse(result['local_saved']);self.assertEqual(self.path.read_bytes(),self.before);push.assert_not_called()
    def test_success_requires_verified_push(self):
        with patch.object(srv,'pull_gist_state',return_value={'state':self.old}),patch.object(srv,'push_gist',return_value=(True,'')):
            _,result=self.post()
        self.assertTrue(result['local_saved']);self.assertTrue(result['cloud_synced'])

class CloudPushTests(unittest.TestCase):
    def test_push_nonzero_and_readback_failure(self):
        from types import SimpleNamespace
        state={'habits':[],'logs':{}}
        with patch.object(srv.subprocess,'run',return_value=SimpleNamespace(returncode=1)),patch.object(srv,'pull_gist_state') as read:
            self.assertFalse(srv.push_gist(state)[0]);read.assert_not_called()
        with patch.object(srv.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(srv,'pull_gist_state',return_value={'state':{'different':1}}):
            self.assertFalse(srv.push_gist(state)[0])
        with patch.object(srv.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(srv,'pull_gist_state',return_value={'state':state}):
            self.assertTrue(srv.push_gist(state)[0])

class CloudFailureTests(unittest.TestCase):
    def test_timeouts_are_reported_without_exception(self):
        from subprocess import TimeoutExpired
        with patch.object(srv.subprocess,'run',side_effect=TimeoutExpired('gh',10)):
            result,error=srv.push_gist({'habits':[],'logs':{}})
        self.assertFalse(result);self.assertTrue(error)

class SaveUnavailableTests(SaveStateTests):
    def test_cloud_unavailable_does_not_push(self):
        with patch.object(srv,'pull_gist_state',return_value=None),patch.object(srv,'push_gist') as push:
            status,result=self.post()
        self.assertEqual(status,200);self.assertTrue(result['local_saved']);self.assertFalse(result['cloud_synced']);push.assert_not_called()
