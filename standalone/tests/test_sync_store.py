"""Mod 同步地基层：存档、时间线、双人合稿，全中文、不用懂 git。

用真 git 跑（CI 三系统自带）；本机没 git 则整份跳过。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync_store
from sync_store import ModSync, SyncError

NEEDS_GIT = shutil.which('git') is not None
HIDDEN = ('commit', 'push', 'pull', 'fetch', 'merge', 'branch', 'clone',
          'rebase', 'stash', 'checkout')


def assert_chinese(test, text):
    lowered = str(text).lower()
    for word in HIDDEN:
        test.assertNotIn(word, lowered, f'用户文案里混进了 git 词：{word}')


class SyncHarness(unittest.TestCase):
    def setUp(self):
        if not NEEDS_GIT:
            self.skipTest('本机没有 git')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'mod').mkdir()
        (self.root / 'relay').mkdir()

    def syncer(self, mod='mod', name='小明的台式机'):
        store = self.root / f'store-{mod}-{name}'
        return ModSync(self.root / mod, store)

    def test_save_and_timeline_roundtrip(self):
        sync = self.syncer()
        sync.start_fresh('小明的台式机')
        (self.root / 'mod' / 'manifest.json').write_text('{"title":"新春"}', encoding='utf-8')
        result = sync.save_snapshot('第一章初稿')
        self.assertTrue(result['saved'])
        again = sync.save_snapshot('再存一次')
        self.assertFalse(again['saved'])
        timeline = sync.timeline()
        self.assertEqual(len(timeline), 1)
        entry = timeline[0]
        self.assertEqual(entry['说明'], '第一章初稿')
        self.assertEqual(entry['作者'], '小明的台式机')
        for value in entry.values():
            assert_chinese(self, value)

    def test_status_keys_are_chinese(self):
        sync = self.syncer()
        sync.start_fresh()
        (self.root / 'mod' / 'a.json').write_text('{}', encoding='utf-8')
        state = sync.current_state()
        self.assertEqual(set(state), {'已修改', '新增', '已删除'})
        self.assertEqual(state['新增'], ['a.json'])
        sync.save_snapshot('初版')
        (self.root / 'mod' / 'a.json').write_text('{"x":1}', encoding='utf-8')
        (self.root / 'mod' / 'a.json').with_name('gone.json')
        state = sync.current_state()
        self.assertEqual(state['已修改'], ['a.json'])

    def test_two_devices_sync_both_ways(self):
        leader = self.syncer(name='小明')
        leader.start_fresh('小明')
        leader.set_relay(self.root / 'relay')
        (self.root / 'mod' / 'story.txt').write_text('开头', encoding='utf-8')
        leader.save_snapshot('开头写完')
        report = leader.sync()
        self.assertTrue(report['已发送'])

        (self.root / 'mod2').mkdir()
        member = ModSync(self.root / 'mod2', self.root / 'store-红')
        member.join(self.root / 'relay', '小红')
        self.assertEqual((self.root / 'mod2' / 'story.txt').read_text(encoding='utf-8'), '开头')

        (self.root / 'mod2' / 'chat.txt').write_text('闲聊', encoding='utf-8')
        member.save_snapshot('闲聊写完')
        member.sync()
        report = leader.sync()
        self.assertEqual((self.root / 'mod' / 'chat.txt').read_text(encoding='utf-8'), '闲聊')
        names = [entry['说明'] for entry in leader.timeline()]
        self.assertIn('开头写完', names)
        self.assertIn('闲聊写完', names)

    def test_same_line_both_kept(self):
        leader = self.syncer(name='小明')
        leader.start_fresh('小明')
        leader.set_relay(self.root / 'relay')
        target = self.root / 'mod' / 'line.txt'
        target.write_text('原句', encoding='utf-8')
        leader.save_snapshot('底稿')
        leader.sync()

        (self.root / 'mod2').mkdir()
        member = ModSync(self.root / 'mod2', self.root / 'store-红')
        member.join(self.root / 'relay', '小红')

        target.write_text('小明的改法', encoding='utf-8')
        leader.save_snapshot('小明改')
        leader.sync()
        (self.root / 'mod2' / 'line.txt').write_text('小红的改法', encoding='utf-8')
        member.save_snapshot('小红改')
        report = member.sync()

        self.assertEqual(len(report['待选']), 1)
        pick = report['待选'][0]
        self.assertEqual(pick['文件'], 'line.txt')
        assert_chinese(self, pick['处理'])
        # 我的留原地，对方的进待选，两边都没丢。
        self.assertEqual((self.root / 'mod2' / 'line.txt').read_text(encoding='utf-8'), '小红的改法')
        aside = Path(pick['对方版本'])
        self.assertTrue(aside.is_file())
        self.assertEqual(aside.read_text(encoding='utf-8'), '小明的改法')
        self.assertTrue(report['已发送'])

    def test_join_needs_empty_folder(self):
        sync = self.syncer()
        sync.start_fresh()
        (self.root / 'mod' / 'x.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(SyncError) as caught:
            sync.join(self.root / 'relay')
        assert_chinese(self, caught.exception.message)

    def test_relay_rejects_nonempty_folder(self):
        (self.root / 'relay' / 'stray.txt').write_text('x', encoding='utf-8')
        sync = self.syncer()
        sync.start_fresh()
        with self.assertRaises(SyncError) as caught:
            sync.set_relay(self.root / 'relay')
        self.assertIn('空文件夹', caught.exception.message)

    def test_sync_without_relay_guidance(self):
        sync = self.syncer()
        sync.start_fresh()
        with self.assertRaises(SyncError) as caught:
            sync.sync()
        self.assertIn('中转站', caught.exception.message)
        assert_chinese(self, caught.exception.message)

    def test_missing_git_message_is_chinese(self):
        with patch.object(sync_store.shutil, 'which', return_value=None):
            sync = ModSync(self.root / 'mod', self.root / 'store-nogit')
            with self.assertRaises(SyncError) as caught:
                sync.start_fresh()
        self.assertIn('Git', caught.exception.message)
        lowered = caught.exception.message.lower()
        for word in ('commit', 'push', 'pull', 'merge', 'branch', 'clone', 'rebase'):
            self.assertNotIn(word, lowered)

    def test_backups_and_temp_never_synced(self):
        sync = self.syncer()
        sync.start_fresh()
        private = self.root / 'mod' / 'StudentAgeStudio' / 'Backups'
        private.mkdir(parents=True)
        (private / 'full.json').write_text('{}', encoding='utf-8')
        (self.root / 'mod' / 'notes.tmp').write_text('x', encoding='utf-8')
        (self.root / 'mod' / 'keep.json').write_text('{}', encoding='utf-8')
        sync.save_snapshot('带私货存一版')
        sync.set_relay(self.root / 'relay')
        sync.sync()
        (self.root / 'mod3').mkdir()
        other = ModSync(self.root / 'mod3', self.root / 'store-3')
        other.join(self.root / 'relay', '第三台')
        names = sorted(p.relative_to(self.root / 'mod3').as_posix()
                       for p in (self.root / 'mod3').rglob('*') if p.is_file())
        self.assertIn('keep.json', names)
        self.assertFalse(any('Backups' in name or name.endswith('.tmp') for name in names))

    def test_size_warning_small_is_quiet(self):
        sync = self.syncer()
        sync.start_fresh()
        self.assertEqual(sync.size_warning(), '')


if __name__ == '__main__':
    unittest.main()
