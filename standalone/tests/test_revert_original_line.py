"""A mod event that opens on an original line must return that line to the base game, not delete it."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as b
import test_original_dialogue as fixture
from test_original_dialogue import ORIGINAL_TALKS, talk


class RevertOriginalLineTests(unittest.TestCase):
    setUp = fixture.OriginalDialogueTests.setUp
    write = fixture.OriginalDialogueTests.write

    def override_original_line(self):
        """The reporter's shape: a mod event starts on original line 1001001, whose follow-up is mod content."""
        revision = self.store.load(self.ident)['revision']
        self.store.save({'projectId': self.ident, 'revision': revision,
            'events': {'1001': dict(fixture.ORIGINAL_EVENTS['1001']),
                       '1991040': {'id': 1991040, 'title': '模组事件', 'type': 1, 'talkId': [1001001], 'rate': 1, 'maxcount': 1, 'effect': [], 'condition': []}},
            'talks': {**{key: dict(value) for key, value in ORIGINAL_TALKS.items()},
                      '1001001': {**ORIGINAL_TALKS['1001001'], 'nextTalk': [9000001]},
                      '9000001': talk(9000001, '模组第一句')}})
        loaded = self.store.load(self.ident)
        self.assertEqual(loaded['talks']['1001001']['nextTalk'], [9000001], 'the mod override is in effect')

    def save_without_the_event(self, extra, deleted=(9000001,), replacements=None):
        loaded = self.store.load(self.ident)
        talks = {key: value for key, value in loaded['talks'].items() if key not in ('1001001', '9000001')}
        events = {key: value for key, value in loaded['events'].items() if key != '1991040'}
        self.store.save({'projectId': self.ident, 'revision': loaded['revision'], 'events': events, 'talks': talks,
                         'deletedIds': list(deleted), 'replacements': replacements if replacements is not None else {'9000001': []}, **extra})

    def test_reverted_line_leaves_the_mod_without_becoming_a_deletion(self):
        self.override_original_line()
        self.save_without_the_event({'revertedTalks': [1001001]})
        markers = b.flat_deletions(b.read_json(self.project.path / 'StudentAgeStudio/deleted-talks.json', {}))
        self.assertIn('9000001', markers, 'the line the mod wrote is deleted')
        self.assertNotIn('1001001', markers, 'an original line returned to the game is not a deletion')
        stored = json.loads((self.cfg / 'TalkCfg.json').read_text(encoding='utf-8'))
        self.assertNotIn('1001001', stored, 'the mod stops overriding the original line')
        self.assertNotIn('9000001', stored)
        loaded = self.store.load(self.ident)
        self.assertEqual(loaded['talks']['1001001']['content'], '原版一', 'the original line plays its own text')
        self.assertEqual(loaded['talks']['1001001']['nextTalk'], [1001002], 'and its original follow-up')

    def test_an_existing_tombstone_is_cleared_by_the_revert(self):
        self.override_original_line()
        self.save_without_the_event({}, deleted=(9000001, 1001001), replacements={'9000001': [], '1001001': [1001002]})
        markers = b.flat_deletions(b.read_json(self.project.path / 'StudentAgeStudio/deleted-talks.json', {}))
        self.assertIn('1001001', markers, 'declaring the original line as deleted still tombstones it')
        self.save_without_the_event({'revertedTalks': [1001001]})
        markers = b.flat_deletions(b.read_json(self.project.path / 'StudentAgeStudio/deleted-talks.json', {}))
        self.assertNotIn('1001001', markers, 'the stale deletion record is dropped when the line is returned')
        stored = json.loads((self.cfg / 'TalkCfg.json').read_text(encoding='utf-8'))
        self.assertNotIn('1001001', stored, 'the empty override that deleted the original line is gone')
        self.write('EvtCfg', {'1001': dict(fixture.ORIGINAL_EVENTS['1001'])})
        self.assertEqual(self.store.load(self.ident)['talks']['1001001']['nextTalk'], [1001002],
                         'the original event plays its own line again once its entry is restored')

    def test_invalid_reverted_ids_are_refused_before_any_write(self):
        self.override_original_line()
        before = (self.cfg / 'TalkCfg.json').read_text(encoding='utf-8')
        for bad in ({'revertedTalks': ['abc']}, {'revertedTalks': [True]}, {'revertedTalks': '1001001'}):
            self.assertRaises(b.ApiError, self.save_without_the_event, bad)
        self.assertEqual((self.cfg / 'TalkCfg.json').read_text(encoding='utf-8'), before, 'a refused save changes nothing')


if __name__ == '__main__':
    unittest.main()
