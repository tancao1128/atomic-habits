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
        for k,v in [('STATE_FILE',self.path),('DATA_DIR',self.root),('BACKUP_DIR',self.backups),('CLOUD_META_FILE',self.root/'meta.json'),('BASELINE_FILE',self.root/'baseline.json')]:
            patcher=patch.object(srv,k,v);patcher.start();self.addCleanup(patcher.stop)
    def post(self, expected=None):
        from urllib.request import Request
        from urllib.error import HTTPError
        server=srv.ThreadingHTTPServer(('127.0.0.1',0),srv.AtomicHabitHandler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            req=Request('http://127.0.0.1:%d/api/state'%server.server_port,data=json.dumps({'state':self.new,'expected_state':self.old if expected is None else expected}).encode(),method='POST',headers={'Content-Type':'application/json'})
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
    def test_stale_window_cannot_replace_newer_state(self):
        latest={'habits':[],'logs':{'newer':{}}};self.path.write_text(json.dumps(latest))
        with patch.object(srv,'pull_gist_state') as read,patch.object(srv,'push_gist') as push:
            code,result=self.post()
        self.assertEqual(code,409);self.assertFalse(result['local_saved']);self.assertEqual(json.loads(self.path.read_text()),latest)
        read.assert_not_called();push.assert_not_called()
    def test_retry_only_when_cloud_still_matches_saved_baseline(self):
        with patch.object(srv,'pull_gist_state',return_value={'state':self.old}),patch.object(srv,'push_gist',side_effect=[(False,'offline'),(True,'')]) as push:
            self.assertFalse(self.post()[1]['cloud_synced'])
            code,result=self.post(expected=self.new)
        self.assertEqual(code,200);self.assertTrue(result['cloud_synced']);self.assertEqual(push.call_count,2)

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

class PracticeStateServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.habits_path=self.root/'habits_state.json'
        self.practice_path=self.root/'practice_state.json'
        self.plan_path=self.root/'gym_plan_mock.json'
        self.backups=self.root/'backups'
        self.habits_orig={'habits':[{'id':'hab_move_daily'}],'logs':{}}
        self.habits_path.write_text(json.dumps(self.habits_orig))
        self.habits_bytes=self.habits_path.read_bytes()
        self.old_practice={'meditation_sessions':[],'gym_sessions':[]}
        self.new_practice={
            'meditation_sessions':[{'id':'med_1','date':'2026-10-31','session':'morning','planned_minutes':60,'actual_minutes':45,'completed_at':'2026-10-31T07:15:00Z'}],
            'gym_sessions':[{'id':'gym_1','date':'2026-10-31','split':'Push','exercises':[],'completed_at':'2026-10-31T17:30:00Z'}]
        }
        self.practice_path.write_text(json.dumps(self.old_practice))
        self.plan_obj={'source':'mock_test','note':'Mock plan file','splits':{'Push':{'label':'Push','exercises':[]}}}
        self.plan_path.write_text(json.dumps(self.plan_obj))
        for k,v in [
            ('STATE_FILE',self.habits_path),
            ('PRACTICE_FILE',self.practice_path),
            ('GYM_PLAN_FILE',self.plan_path),
            ('DATA_DIR',self.root),
            ('BACKUP_DIR',self.backups),
            ('CLOUD_META_FILE',self.root/'meta.json'),
            ('PRACTICE_BASELINE_FILE',self.root/'practice_baseline.json'),
            ('DISABLE_GIST_SYNC',False),
        ]:
            p=patch.object(srv,k,v);p.start();self.addCleanup(p.stop)

    def post_practice(self, state=None, expected=None, push_cloud=True):
        from urllib.request import Request
        from urllib.error import HTTPError
        server=srv.ThreadingHTTPServer(('127.0.0.1',0),srv.AtomicHabitHandler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            payload={
                'state':self.new_practice if state is None else state,
                'expected_state':self.old_practice if expected is None else expected,
                'push_cloud':push_cloud
            }
            req=Request('http://127.0.0.1:%d/api/practice'%server.server_port,data=json.dumps(payload).encode(),method='POST',headers={'Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=5) as r:return r.status,json.load(r)
            except HTTPError as e:return e.code,json.load(e)
        finally:
            server.shutdown();server.server_close();t.join()

    def test_get_practice_returns_state_and_plan_without_touching_habits(self):
        with patch.object(srv,'pull_practice_gist_state',return_value={'state':self.old_practice}):
            server=srv.ThreadingHTTPServer(('127.0.0.1',0),srv.AtomicHabitHandler)
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                with urlopen('http://127.0.0.1:%d/api/practice'%server.server_port,timeout=3) as r:
                    data=json.load(r)
            finally:
                server.shutdown();server.server_close();t.join()
        self.assertEqual(data['state'],self.old_practice)
        self.assertEqual(data['plan'],self.plan_obj)
        self.assertEqual(data['cloud_status'],'matches')
        self.assertEqual(self.habits_path.read_bytes(),self.habits_bytes)

    def test_save_practice_without_cloud_push_saves_locally_only(self):
        with patch.object(srv,'push_practice_gist') as push:
            code,res=self.post_practice(push_cloud=False)
        self.assertEqual(code,200)
        self.assertTrue(res['local_saved'])
        self.assertFalse(res['cloud_synced'])
        push.assert_not_called()
        self.assertEqual(json.loads(self.practice_path.read_text()),self.new_practice)
        self.assertEqual(self.habits_path.read_bytes(),self.habits_bytes)

    def test_save_practice_on_session_end_pushes_and_reports_cloud_failure(self):
        with patch.object(srv,'pull_practice_gist_state',return_value={'state':self.old_practice}),patch.object(srv,'push_practice_gist',return_value=(False,'Gist Practice offline')):
            code,res=self.post_practice(push_cloud=True)
        self.assertEqual(code,200)
        self.assertTrue(res['local_saved'])
        self.assertFalse(res['cloud_synced'])
        self.assertIn('offline',res['cloud_error'])
        self.assertEqual(json.loads(self.practice_path.read_text()),self.new_practice)
        self.assertEqual(self.habits_path.read_bytes(),self.habits_bytes)

    def test_save_practice_stale_expected_returns_409(self):
        newer={'meditation_sessions':[{'id':'newer'}],'gym_sessions':[]}
        self.practice_path.write_text(json.dumps(newer))
        with patch.object(srv,'push_practice_gist') as push:
            code,res=self.post_practice(push_cloud=True)
        self.assertEqual(code,409)
        self.assertFalse(res['local_saved'])
        self.assertEqual(json.loads(self.practice_path.read_text()),newer)
        push.assert_not_called()

