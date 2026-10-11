"""协作组与号段：成员管理、分片公式、分配约束。

单人（无组）行为必须和以前逐位一致；多人时同表分配永不跨段。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as b
import sync_store
from sync_store import ModSync, SyncError

# 和 record-ids.js:shardRange 逐位一致的向量，改一处必须两处一起改。
VECTORS = [
    # (start, end, step, shard, expected_lo, expected_hi)
    (1000000, 1999999, 1, 0, 1000000, 1000976),
    (1000000, 1999999, 1, 1, 1000977, 1001953),
    (1000000, 1999999, 1, 1023, 1999471, 1999999),
    (1000001, 1999999, 1000, 5, 1005001, 1005001),
    (3000000, 3999999, 1, 1023, 3999471, 3999999),
]

A = {'id': 'a1', 'name': '小明'}
B = {'id': 'b2', 'name': '小红'}


class ShardMathTests(unittest.TestCase):
    def test_vectors(self):
        for start, end, step, shard, lo, hi in VECTORS:
            with self.subTest(shard=shard, step=step):
                self.assertEqual(sync_store.shard_interval(start, end, step, shard), (lo, hi))

    def test_shards_cover_full_range_without_overlap(self):
        intervals = [sync_store.shard_interval(1000000, 1999999, 1, shard) for shard in range(1024)]
        self.assertEqual(intervals[0][0], 1000000)
        self.assertEqual(intervals[-1][1], 1999999)
        for (prev_lo, prev_hi), (lo, hi) in zip(intervals, intervals[1:]):
            self.assertEqual(lo, prev_hi + 1)
            self.assertLessEqual(lo, hi)
            _ = prev_lo

    def test_step_alignment(self):
        for shard in (0, 7, 999):
            lo, hi = sync_store.shard_interval(1000001, 1999999, 1000, shard)
            self.assertEqual((lo - 1000001) % 1000, 0)
            self.assertLessEqual(lo, hi)


class TeamFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.mod = Path(temporary.name).resolve() / 'mod'
        self.mod.mkdir()

    def test_solo_has_no_slot_and_no_side_effects(self):
        self.assertIsNone(sync_store.active_slot(self.mod))
        self.assertFalse((self.mod / 'StudentAgeStudio' / 'sync-team.json').exists())

    def test_lifecycle(self):
        team, created = sync_store.ensure_team(self.mod, A)
        self.assertTrue(created)
        self.assertEqual(team['members']['a1']['slot'], 0)
        self.assertEqual(team['owner'], 'a1')
        again, created = sync_store.ensure_team(self.mod, A)
        self.assertFalse(created)
        # 不足两人不分段
        self.assertIsNone(sync_store.active_slot(self.mod))
        team = sync_store.add_member(self.mod, B)
        self.assertNotEqual(team['members']['b2']['slot'], 0)
        self.assertEqual(sync_store.team_collisions(team), [])
        # 坏文件当没组
        (self.mod / 'StudentAgeStudio' / 'sync-team.json').write_bytes(b'{broken')
        self.assertIsNone(sync_store.read_team(self.mod))

    def test_collision_detect_and_reroll(self):
        sync_store.ensure_team(self.mod, A)
        sync_store.add_member(self.mod, B)
        sync_store.set_member_slot(self.mod, 'b2', 0)
        team = sync_store.read_team(self.mod)
        self.assertEqual(sync_store.team_collisions(team), [['小明', '小红']])
        sync_store.reroll_slot(self.mod, 'b2')
        self.assertEqual(sync_store.team_collisions(sync_store.read_team(self.mod)), [])

    def test_remove_member_and_owner_handover(self):
        sync_store.ensure_team(self.mod, A)
        sync_store.add_member(self.mod, B)
        with self.assertRaises(SyncError):
            sync_store.set_member_slot(self.mod, 'b2', 9999)
        sync_store.remove_member(self.mod, 'b2')
        team = sync_store.read_team(self.mod)
        self.assertEqual(list(team['members']), ['a1'])
        sync_store.remove_member(self.mod, 'a1')
        self.assertIsNone(sync_store.read_team(self.mod))
        # 主人走时组交给剩下的人
        sync_store.ensure_team(self.mod, A)
        sync_store.add_member(self.mod, B)
        sync_store.remove_member(self.mod, 'a1')
        self.assertEqual(sync_store.read_team(self.mod)['owner'], 'b2')


class TeamAllocateTests(unittest.TestCase):
    def setUp(self):
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
        self.ident = self.store.create('号段')['id']
        self.project = self.store.project(self.ident)

    def write_team(self, members):
        sync_store.write_team(self.project.path, {'version': 1, 'owner': 'a1', 'members': members})

    def write_device(self, device_id):
        from sync_store import default_store_dir
        root = default_store_dir(self.project.path)
        root.mkdir(parents=True, exist_ok=True)
        (root / 'device.json').write_text(
            '{"id": "%s", "name": "机"}' % device_id, encoding='utf-8')

    def test_solo_allocate_unchanged(self):
        ident = self.store.record_ids.allocate('ItemCfg', {}, project_path=self.project.path)
        self.assertTrue(1000000 <= ident <= 1999999)
        public = self.store.record_ids.public(self.ident)
        self.assertFalse(public['syncShard']['active'])

    def test_team_allocate_stays_in_shard(self):
        self.write_team({'a1': {'name': '甲', 'slot': 5, 'joinedAt': ''},
                         'b2': {'name': '乙', 'slot': 9, 'joinedAt': ''}})
        self.write_device('a1')
        public = self.store.record_ids.public(self.ident)
        self.assertTrue(public['syncShard']['active'])
        self.assertEqual(public['syncShard']['shard'], 5)
        for _ in range(10):
            ident = self.store.record_ids.allocate('ItemCfg', {}, project_path=self.project.path)
            self.assertTrue(1004885 <= ident <= 1005861, ident)
        # 派生表跟主人走，不限段
        talk = self.store.record_ids.allocate('TalkCfg', {}, owner=7, project_path=self.project.path)
        self.assertTrue(7001 <= talk <= 7999)
        # 不在组里的设备不限段
        self.write_device('stranger')
        ident = self.store.record_ids.allocate('ItemCfg', {}, project_path=self.project.path)
        self.assertTrue(1000000 <= ident <= 1999999)

    def test_join_adds_member_and_commits(self):
        relay = self.root / 'relay'
        relay.mkdir()
        syncer = ModSync(self.project.path)
        syncer.start_fresh('小明')
        sync_store.ensure_team(self.project.path, {'id': 'mine', 'name': '小明'})
        syncer.save_snapshot('底稿')
        syncer.set_relay(relay)
        syncer.sync()
        (self.root / 'mod2').mkdir()
        other = ModSync(self.root / 'mod2', self.root / 'store-红')
        other.join(relay, '小红')
        team = sync_store.read_team(self.root / 'mod2')
        self.assertEqual(len(team['members']), 2)
        names = [entry['说明'] for entry in other.timeline()]
        self.assertTrue(any('新成员加入' in name for name in names))


if __name__ == '__main__':
    unittest.main()
