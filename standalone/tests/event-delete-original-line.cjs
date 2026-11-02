'use strict';
// Deleting an event that opens on an original-game line must return that line instead of deleting it.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const I = require('../indexed-talks.js'), R = I.Remote, Timeline = require('../timeline.js'), Branches = require('../branches.js'), Ownership = require('../event-ownership.js');
const source = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const take = (a, b) => source.slice(source.indexOf(a), source.indexOf(b));
const ids = value => Array.isArray(value) ? value.map(Number).filter(Number.isFinite) : [];
const copy = value => JSON.parse(JSON.stringify(value));

// The reporter's shape: the mod copies original line 1856 and hangs its own first line after it.
const rows = {
  1856: {id: 1856, content: '作业好多啊，想去找琪露诺玩', nextTalk: [9000001], nextTalk2: [], option: []},
  9000001: {id: 9000001, content: '模组第一句', nextTalk: [], nextTalk2: [], option: []},
};
const factories = {
  plain: () => copy(rows),
  indexed: () => I.create(Object.entries(rows).map(([id, row]) => [id, JSON.stringify(row)])),
};

function harness(kind) {
  const S = {project: {id: 'revert-case'}, revision: 'r1',
    doc: {talks: factories[kind](), events: {1991040: {id: 1991040, title: '模组事件', talkId: [1856], options: []}}, options: {}, persons: {}, talkOwners: {},
      audioCues: {sfx: {}, bgm: [], nativeAudio: {}}},
    order: [1856, 9000001], deleted: [], reverted: [], replacements: {}, premises: {}, branchFolders: {},
    pinned: {talks: new Set(), options: new Set(), events: new Set()}, idMappings: {}, undo: [], redo: [],
    // 1856 is in the mod file and also in the base game: the editor reports it in catalogAll.
    localIds: {talks: ['1856', '9000001']}, catalogAll: {talks: new Set(['1856']), options: new Set(), events: new Set()},
    catalogTalkIds: new Set(), catalogIds: {talks: new Set(), options: new Set(), events: new Set()},
    event: 1991040, selected: 1856, activeFolder: null, eventGrades: {},
    actionEvents: {}, actions: {}, interactions: {}, saving: false, dirty: true, saved: ''};
  S.savedOwners = S.doc.talkOwners;
  const box = {S, ids, values: value => Object.values(value || {}), IndexedTalks: I, Remote: R, clone: I.clone, Number, Object, Array, Set, Map, JSON, String, Math, Boolean, Error,
    Timeline, Branches, StudentAgeEventOwnership: Ownership, StudentAgeEventBindings: {clearSocialBindings() {}, syncSocialEffects() {}},
    editable: () => true, mutate: (label, change) => { change(); return true; }, toast: () => {}, removePremises: () => {},
    window: {STUDIO_EVENTS: {refresh: () => {}}}, document: {activeElement: null, querySelector: () => null},
    textDirtyTimer: null, renumberDeferred: false, renderChrome: () => {}, storySelection: {render: () => {}}, scheduleRenumber: () => {},
    storyClaims: new Map(), fail: () => {}, updateDirty: () => {}, api: async () => ({ok: true, revision: 'r2'}),
    pinnedSnapshot: () => ({talks: [], options: [], events: []})};
  vm.createContext(box);
  vm.runInContext(take('function signatureFromParts(', '// Undo keeps') + take('function sameValue', 'function plainValue') +
    take('function ownersDiffer', 'function updateDirty') +
    take('function changedStoryPayload(', 'function unassignedTalkIds(') + take('function deleteEvents(', 'function deleteEvent('), box);
  S.saved = box.currentSignature();
  return {S, box};
}

for (const kind of Object.keys(factories)) {
  const {S, box} = harness(kind);
  assert.equal(box.deleteEvents([1991040]), true, kind + ': the delete runs');
  assert.deepEqual(copy(S.deleted), [9000001], kind + ': only the line the mod wrote is deleted');
  assert.deepEqual(copy(S.reverted), [1856], kind + ': the original line is returned to the game instead');
  assert.deepEqual(copy(S.replacements), {'9000001': []}, kind + ': links are rewired around the deleted line only');
  assert.deepEqual(copy(S.order), [], kind + ': both rows leave the mod list');
  assert.deepEqual(Object.keys(S.doc.talks).map(Number), [], kind + ': neither row stays in the mod table');

  const payload = copy(box.changedStoryPayloadFrom(I.parse(S.saved)));
  assert.deepEqual(payload.revertedTalks, [1856], kind + ': the save tells the server to un-override the line');
  assert.deepEqual(payload.deletedIds, [9000001], kind + ': the original line is not in the deletion list');
  if (payload.talkPatch) assert.deepEqual(payload.talkPatch.deleted, [9000001], kind + ': the delta excludes the returned line');
}

// Without a base-game catalog nothing is known to be original, so the old behaviour must stand unchanged.
{
  const {S, box} = harness('plain');
  S.catalogAll = {talks: new Set(), options: new Set(), events: new Set()};
  box.deleteEvents([1991040]);
  assert.deepEqual(copy(S.deleted), [1856, 9000001], 'no catalog: both rows are treated as mod content');
  assert.deepEqual(copy(S.reverted), [], 'no catalog: nothing is claimed as an original line');
}

// A line another event still shares is left alone, exactly as before.
{
  const {S, box} = harness('plain');
  S.doc.events[1991041] = {id: 1991041, title: '另一个模组事件', talkId: [1856], options: []};
  box.deleteEvents([1991040]);
  assert.deepEqual(copy(S.deleted), [], 'a line the other event still reaches stays in the mod');
  assert.deepEqual(copy(S.reverted), [], 'and the shared original line is not un-overridden either');
}

console.log('event-delete-original-line: ok');
