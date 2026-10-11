"""同步任务服务：定稿、回退、设备、中转站、后台任务。

 jobs 跑真线程＋真 git（CI 三系统自带）；本机没 git 整份跳过。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as b
from sync_service import SyncService, SyncJob
from sync_store import ModSync, SyncError

NEEDS_GIT = shutil.which('git') is not None


class SyncServiceHarness(unittest.TestCase):
    def setUp(self):
        if not NEEDS_GIT:
            self.skipTest('本机没有 git')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        from unittest.mock import patch as _patch
        env = _patch.dict(os.environ, {'STUDIO_CACHE_ROOT': str(self.root / 'Cache'),
                                       'STUDIO_USER_DATA_ROOT': str(self.root / 'User'),
                                       'STUDIO_DISPLAY_SETTINGS': str(self.root / 'display.json')})
        env.start()
        self.addCleanup(env.stop)
        self.store = b.StudioStore(self.root / 'Mods', self.root / 'Workshop', self.root / 'Game')
        self.ident = self.store.create('同步任务')['id']
        self.project = self.store.project(self.ident)
        (self.project.path / 'story.txt').write_text('开头', encoding='utf-8')
        self.service = SyncService()
        self.relay = self.root / 'relay'
        self.relay.mkdir()

    def payload(self, **overrides):
        body = {'projectId': self.ident, 'revision': self.store.revision(self.project)}
        body.update(overrides)
        return body

    def joined(self, name='小明'):
        self.service.set_relay(self.store, b, self.payload(path=str(self.relay)))
        syncer = ModSync(self.project.path)
        syncer.start_fresh(name)
        return syncer

    def wait_job(self, job_id, timeout=120):
        deadline = time.time() + timeout
        while True:
            state = self.service.status(job_id)
            self.assertIsNotNone(state)
            if state['state'] in ('done', 'error', 'cancelled'):
                return state
            self.assertLess(time.time(), deadline, '任务卡住了：' + state['state'])
            time.sleep(0.2)

    def test_save_rejects_stale_revision(self):
        with self.assertRaises(b.ApiError):
            self.service.save(self.store, b, self.payload(revision='stale'))
        result = self.service.save(self.store, b, self.payload(summary='第一版'))
        self.assertTrue(result.get('saved') or 'revision' in result)

    def test_save_summary_bounded(self):
        with self.assertRaises(b.ApiError) as caught:
            self.service.save(self.store, b, self.payload(summary='x' * 201))
        self.assertEqual(caught.exception.code, 'summary_too_long')

    def test_relay_inside_mod_refused(self):
        with self.assertRaises(b.ApiError) as caught:
            self.service.set_relay(self.store, b, self.payload(path=str(self.project.path / 'sub')))
        self.assertIn('Mod 文件夹', caught.exception.message)

    def test_relay_needs_absolute_path(self):
        with self.assertRaises(b.ApiError):
            self.service.set_relay(self.store, b, self.payload(path='relative/path'))

    def test_overview_before_and_after_join(self):
        before = self.service.overview(self.store, b, self.ident)
        self.assertFalse(before['joined'])
        self.assertEqual(before['pendingPicks'], [])
        self.joined()
        syncer = ModSync(self.project.path)
        syncer.save_snapshot('底稿')
        job = self.service.start(self.store, self.ident)
        done = self.wait_job(job['jobId'])
        self.assertEqual(done['state'], 'done')
        after = self.service.overview(self.store, b, self.ident)
        self.assertTrue(after['joined'])
        self.assertEqual(after['relay'], str(self.relay.resolve()))
        self.assertEqual(after['snapshotCount'], 1)
        self.assertEqual(after['device']['name'], '小明')
        self.assertIn('revision', done)

    def test_job_unknown_and_cancel_unknown(self):
        self.assertIsNone(self.service.status('nope'))
        self.assertIsNone(self.service.cancel('nope'))

    def test_cancel_marks_job(self):
        from sync_service import SyncJob as Job
        job = Job('j1', self.ident)
        job.state = 'running'
        self.service._jobs['j1'] = job
        result = self.service.cancel('j1')
        self.assertEqual(result['state'], 'cancelling')
        self.service.close()
        self.assertEqual(job.state, 'cancelled')

    def test_pick_theirs_applies_and_commits(self):
        syncer = self.joined('小明')
        target = self.project.path / 'line.txt'
        target.write_text('原句', encoding='utf-8')
        syncer.save_snapshot('底稿')
        self.wait_job(self.service.start(self.store, self.ident)['jobId'])
        (self.root / 'mod2').mkdir()
        member = ModSync(self.root / 'mod2', self.root / 'store-红')
        member.join(self.relay, '小红')
        (self.root / 'mod2' / 'line.txt').write_text('小红的改法', encoding='utf-8')
        member.save_snapshot('小红改')
        member.sync()
        target.write_text('小明的改法', encoding='utf-8')
        syncer.save_snapshot('小明改')
        job = self.service.start(self.store, self.ident)
        done = self.wait_job(job['jobId'])
        self.assertEqual(done['state'], 'done')
        picks = self.service.overview(self.store, b, self.ident)['pendingPicks']
        self.assertEqual(len(picks), 1)
        result = self.service.resolve_pick(
            self.store, b, {'projectId': self.ident, 'file': 'line.txt', 'choice': '要对方的'})
        self.assertEqual(result['剩余待选'], 0)
        self.assertEqual(target.read_text(encoding='utf-8'), '小红的改法')
        names = [entry['说明'] for entry in ModSync(self.project.path).timeline()]
        self.assertTrue(any('合稿定稿' in name for name in names))

    def test_pick_mine_keeps_file(self):
        syncer = self.joined('小明')
        target = self.project.path / 'line.txt'
        target.write_text('原句', encoding='utf-8')
        syncer.save_snapshot('底稿')
        self.wait_job(self.service.start(self.store, self.ident)['jobId'])
        (self.root / 'mod2').mkdir()
        member = ModSync(self.root / 'mod2', self.root / 'store-红')
        member.join(self.relay, '小红')
        (self.root / 'mod2' / 'line.txt').write_text('他的', encoding='utf-8')
        member.save_snapshot('他改')
        member.sync()
        target.write_text('我的', encoding='utf-8')
        syncer.save_snapshot('我改')
        job = self.service.start(self.store, self.ident)
        done = self.wait_job(job['jobId'])
        self.assertEqual(done['state'], 'done')
        result = self.service.resolve_pick(
            self.store, b, {'projectId': self.ident, 'file': 'line.txt', 'choice': '留我的'})
        self.assertEqual(result['剩余待选'], 0)
        self.assertEqual(target.read_text(encoding='utf-8'), '我的')

    def test_pick_missing_rejected(self):
        with self.assertRaises(b.ApiError):
            self.service.resolve_pick(
                self.store, b, {'projectId': self.ident, 'file': 'nope.txt', 'choice': '留我的'})

    def test_restore_roundtrip(self):
        syncer = self.joined()
        target = self.project.path / 'story.txt'
        target.write_text('第一版', encoding='utf-8')
        syncer.save_snapshot('第一版')
        target.write_text('第二版', encoding='utf-8')
        syncer.save_snapshot('第二版')
        target.write_text('没存的第三版', encoding='utf-8')
        first = [entry for entry in syncer.timeline() if entry['说明'] == '第一版'][0]
        result = self.service.restore(self.store, b, {'projectId': self.ident, 'id': first['编号']})
        self.assertTrue(result['ok'])
        self.assertEqual(target.read_text(encoding='utf-8'), '第一版')
        names = [entry['说明'] for entry in syncer.timeline()]
        self.assertIn('回退前自动存档', names)
        self.assertTrue(any(name.startswith('回到存档') for name in names))

    def test_restore_bad_id_rejected(self):
        self.joined()
        with self.assertRaises(b.ApiError):
            self.service.restore(self.store, b, {'projectId': self.ident, 'id': 'zzz'})

    def test_rename_device(self):
        self.joined('小明')
        result = self.service.rename_device(self.store, b, {'projectId': self.ident, 'name': '主力机'})
        self.assertEqual(result['device']['name'], '主力机')
        with self.assertRaises(b.ApiError):
            self.service.rename_device(self.store, b, {'projectId': self.ident, 'name': ''})
        with self.assertRaises(b.ApiError):
            self.service.rename_device(self.store, b, {'projectId': self.ident, 'name': 'x' * 31})

    def test_workshop_project_rejected(self):
        with self.assertRaises(b.ApiError):
            self.service.start(self.store, 'workshop:dead')


if __name__ == '__main__':
    unittest.main()
