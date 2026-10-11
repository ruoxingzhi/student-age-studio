"""多人协作与多平台同步的地基层：借用 git 存历史，对用户 полностью 中文。

用户永远看不到 git：存档、时间线、同步、合稿单、待选、平行世界。
git 只是一个"账本"，装在本机、中转站里，不进 Mod，不进游戏，不上传工坊。
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

MAIN_LINE = 'main'
DEVICE_FILE = 'device.json'
TEAM_FILE = 'StudentAgeStudio/sync-team.json'
SHARDS = 1024
PICK_DIR = '合稿待选'
PICKS_FILE = 'picks.json'
QUEUE_DIR = '待发送'
LARGE_WARN_BYTES = 900 * 1024 * 1024
DEFAULT_TIMEOUT = 300
SUMMARY_MAX = 200
DEVICE_NAME_MAX = 30


class _Cancelled(Exception):
    pass


class SyncProgress:
    """同步进度与协作取消。UI 轮询时读 phase/percent。"""

    def __init__(self):
        self.phase = '准备同步'
        self.percent = 0
        self.cancelled = threading.Event()

    def set(self, phase, percent):
        self.phase = str(phase)
        self.percent = max(0, min(100, int(percent)))

    def check(self):
        if self.cancelled is not None and self.cancelled.is_set():
            raise _Cancelled()

# 用户可见文案里不出现的词（内部名字可以用，绝不展示）。
_HIDDEN_WORDS = ('commit', 'push', 'pull', 'fetch', 'merge', 'branch', 'clone',
                 'rebase', 'stash', 'checkout')


class SyncError(Exception):
    """带中文说明和稳定编号的同步错误。"""

    def __init__(self, message, code='sync_failed'):
        super().__init__(message)
        self.message, self.code = message, code


def _utcnow():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _localnow():
    return time.strftime('%Y年%m月%d日 %H:%M', time.localtime())


def git_exe():
    """找到本机 git，找不到就用中文指引报错。"""
    found = shutil.which('git')
    if found:
        return found
    raise SyncError('本机没找到 Git，请先安装 Git 再用同步功能（装好后重进编辑器即可）。',
                    'git_missing')


def _run(exe, args, cwd, timeout=DEFAULT_TIMEOUT, binary=False):
    """跑一条 git 命令。超时、中文输出、永不弹窗问密码。"""
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['LC_ALL'] = 'C.UTF-8'
    env['LANG'] = 'C.UTF-8'
    full = [exe, '-c', 'i18n.commitEncoding=UTF-8', '-c', 'i18n.logOutputEncoding=UTF-8', *args]
    options = {}
    if os.name == 'nt':
        options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        completed = subprocess.run(full, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=timeout, **options)
    except FileNotFoundError:
        raise SyncError('本机没找到 Git，请先安装 Git 再用同步功能（装好后重进编辑器即可）。',
                        'git_missing')
    except subprocess.TimeoutExpired:
        raise SyncError('这一步等太久了（可能是文件太大或网盘还没跟上），请稍后再点同步。',
                        'sync_timeout')
    if binary:
        return completed.returncode, completed.stdout
    return completed.returncode, completed.stdout.decode('utf-8', errors='replace')


def _must(code, output, what):
    if code != 0:
        tail = '\n'.join(output.strip().splitlines()[-3:]) if output.strip() else ''
        raise SyncError(f'{what}没能完成，请稍后再试。{tail}', 'sync_failed')


def _sanitize(name):
    cleaned = ''.join('_' if char in '\\/:*?"<>|\r\n' else char for char in str(name or ''))
    return cleaned.strip(' .') or '未命名'


def default_store_dir(mod_path):
    """本机账本默认位置：用户目录下按 Mod 路径分开。"""
    from storage_paths import user_data_root
    key = hashlib.sha1(os.path.normcase(str(Path(mod_path).resolve())).encode()).hexdigest()[:16]
    return Path(user_data_root()) / 'sync' / key


def shard_interval(start, end, step, shard, shards=SHARDS):
    """号段分片：同一公式前后端各一份，行为逐位一致。

    把 [start, end] 按 step 切成 shards 段，取第 shard 段。
    用完（lo > end）就抛号段用完，不去别家串门。
    """
    count = (end - start) // step + 1
    if count <= 0:
        raise SyncError('编号范围无效。', 'shard_exhausted')
    per = (count + shards - 1) // shards
    lo = start + shard * per * step
    hi = min(end, lo + (per - 1) * step)
    return lo, hi


def read_team(mod_path):
    """读协作组；没有或坏了返回 None（单人模式，不报错）。"""
    try:
        data = json.loads((Path(mod_path) / TEAM_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    members = data.get('members')
    if not isinstance(members, dict):
        return None
    clean = {}
    for key, row in members.items():
        if not isinstance(row, dict):
            continue
        try:
            slot = int(row.get('slot'))
        except (TypeError, ValueError):
            continue
        if not 0 <= slot < SHARDS:
            continue
        clean[str(key)] = {'name': str(row.get('name', ''))[:30], 'slot': slot,
                           'joinedAt': str(row.get('joinedAt', ''))}
    if not clean:
        return None
    return {'version': 1, 'owner': str(data.get('owner', '')), 'members': clean}


def write_team(mod_path, team):
    """原子写协作组文件（网盘同步一半读到坏文件时读端会自动忽略）。"""
    from platform_support import replace_file
    target = Path(mod_path) / TEAM_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name('.' + target.name + '.tmp')
    tmp.write_text(json.dumps(team, ensure_ascii=False, indent=2), encoding='utf-8')
    replace_file(tmp, target)


def read_device_id(mod_path, store_dir=None):
    """只读身份，不创建（读路径零副作用，单人用户无感）。"""
    root = Path(store_dir) if store_dir else default_store_dir(mod_path)
    try:
        data = json.loads((root / DEVICE_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    return str(data.get('id', '')) if isinstance(data, dict) else ''


def active_slot(mod_path, store_dir=None):
    """我的号段；单人（无组／不足两人／我不在组里）返回 None，全程无副作用。"""
    team = read_team(mod_path)
    if team is None or len(team['members']) < 2:
        return None
    entry = team['members'].get(read_device_id(mod_path, store_dir))
    return entry['slot'] if entry else None


def team_collisions(team):
    """撞号段的成员名分组。"""
    by_slot = {}
    for key, row in team['members'].items():
        by_slot.setdefault(row['slot'], []).append(row['name'] or key)
    return sorted(names for names in by_slot.values() if len(names) > 1)


def ensure_team(mod_path, device):
    """主人开组：没有才建，有就原样返回。"""
    team = read_team(mod_path)
    if team is not None:
        return team, False
    team = {'version': 1, 'owner': str(device['id']),
            'members': {str(device['id']): {'name': str(device.get('name', ''))[:30],
                                            'slot': 0, 'joinedAt': _localnow()}}}
    write_team(mod_path, team)
    return team, True


def add_member(mod_path, device):
    """加入组：已有组才进；名字变了顺手更新；随机领一段空号段。"""
    team = read_team(mod_path)
    if team is None:
        return None
    key = str(device['id'])
    if key in team['members']:
        team['members'][key]['name'] = str(device.get('name', ''))[:30]
        write_team(mod_path, team)
        return team
    taken = {row['slot'] for row in team['members'].values()}
    for _ in range(200):
        candidate = secrets.randbelow(SHARDS)
        if candidate not in taken:
            break
    else:
        raise SyncError('号段暂时分完了，请找主人清理成员。', 'shards_full')
    team['members'][key] = {'name': str(device.get('name', ''))[:30],
                            'slot': candidate, 'joinedAt': _localnow()}
    write_team(mod_path, team)
    return team


def reroll_slot(mod_path, device_id):
    """给自己重领一段（撞号段时点一下）。"""
    team = read_team(mod_path)
    if team is None:
        raise SyncError('还没开始协作，不用领号段。', 'team_missing')
    if str(device_id) not in team['members']:
        raise SyncError('你不在协作组里，请重新加入。', 'team_not_member')
    taken = {key: row['slot'] for key, row in team['members'].items() if key != str(device_id)}
    taken = set(taken.values())
    for _ in range(200):
        candidate = secrets.randbelow(SHARDS)
        if candidate not in taken:
            break
    else:
        raise SyncError('号段暂时分完了，请找主人清理成员。', 'shards_full')
    team['members'][str(device_id)]['slot'] = candidate
    write_team(mod_path, team)
    return team


def set_member_slot(mod_path, device_id, shard):
    """主人指定某人号段。"""
    try:
        shard = int(shard)
    except (TypeError, ValueError):
        raise SyncError('号段是 0 到 1023 的整数。', 'shard_invalid')
    if not 0 <= shard < SHARDS:
        raise SyncError('号段是 0 到 1023 的整数。', 'shard_invalid')
    team = read_team(mod_path)
    if team is None:
        raise SyncError('还没开始协作。', 'team_missing')
    if str(device_id) not in team['members']:
        raise SyncError('这个人不在组里，请让他重新加入。', 'team_not_member')
    team['members'][str(device_id)]['slot'] = shard
    write_team(mod_path, team)
    return team


def remove_member(mod_path, device_id):
    """移出成员；主人走时把组交给最早加入的人，人走光了组就散了。"""
    team = read_team(mod_path)
    if team is None:
        raise SyncError('还没开始协作。', 'team_missing')
    key = str(device_id)
    if key not in team['members']:
        raise SyncError('这个人不在组里。', 'team_not_member')
    del team['members'][key]
    if not team['members']:
        try:
            (Path(mod_path) / TEAM_FILE).unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if team['owner'] == key:
        team['owner'] = next(iter(team['members']))
    write_team(mod_path, team)
    return team


def ensure_device(store_dir, display_name=None):
    """本机身份：名字可改，后缀永不重名。"""
    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    target = store_dir / DEVICE_FILE
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if not data.get('id'):
        data['id'] = uuid.uuid4().hex[:8]
    if display_name:
        data['name'] = str(display_name)
    data.setdefault('name', '我的电脑')
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return dict(data)


class ModSync:
    """一个 Mod 的同步器。账本放在本机（store_dir），Mod 文件夹保持干净。"""

    def __init__(self, mod_path, store_dir=None, timeout=DEFAULT_TIMEOUT):
        self.mod = Path(mod_path).resolve()
        self.store = Path(store_dir).resolve() if store_dir else default_store_dir(self.mod)
        self.timeout = timeout
        self.exe = None

    # ---- 内部小件 ----

    def _git(self, *args, timeout=None, binary=False):
        if self.exe is None:
            self.exe = git_exe()
        return _run(self.exe, list(args), self.mod, timeout or self.timeout, binary)

    def _base(self):
        return ['--git-dir', str(self.store / 'repo'), '--work-tree', str(self.mod)]

    def _call(self, *args, timeout=None, binary=False):
        return self._git(*self._base(), *args, timeout=timeout, binary=binary)

    def ready(self):
        """账本建好了吗？没建好时读取类操作返回空，不报错。"""
        try:
            return (self.store / 'repo' / 'HEAD').is_file()
        except OSError:
            return False

    # ---- 建账本 / 加中转站 ----

    def start_fresh(self, display_name=None):
        """新开一个 Mod 的同步：建账本、记身份、写忽略单。"""
        if not self.mod.is_dir():
            raise SyncError('找不到这个 Mod 文件夹，请重新打开 Mod。', 'mod_missing')
        device = ensure_device(self.store, display_name)
        repo = self.store / 'repo'
        if (repo / 'HEAD').is_file():
            return {'device': device, 'already': True}
        repo.mkdir(parents=True, exist_ok=True)
        # Bare 账本：HEAD 直接在顶层，和 --git-dir 的指向一致。
        # （普通 init 会再套一层 .git/，账本就认不出来了。）
        code, output = self._git('init', '--bare', str(repo))
        _must(code, output, '建账本')
        code, output = self._git('--git-dir', str(repo), 'symbolic-ref', 'HEAD',
                                 f'refs/heads/{MAIN_LINE}')
        _must(code, output, '建账本')
        code, output = self._call('config', 'user.name', device['name'])
        _must(code, output, '建账本')
        code, output = self._call('config', 'user.email', device['id'] + '@studio.local')
        _must(code, output, '建账本')
        (repo / 'info' / 'exclude').write_text(
            'StudentAgeStudio/Backups/\n*.tmp\n*.log\n.save.lock\nPreviewCache/\n', encoding='utf-8')
        return {'device': device, 'already': False}

    def set_relay(self, relay_dir):
        """把一个文件夹当中转站（网盘里选一个空夹）。没有就帮你建成可用的样子。"""
        relay = Path(relay_dir).resolve()
        relay.mkdir(parents=True, exist_ok=True)
        if not (relay / 'HEAD').is_file():
            if any(relay.iterdir()):
                raise SyncError('这个文件夹里有别的东西，请选空文件夹当中转站。', 'relay_not_empty')
            code, output = self._git('init', '--bare', str(relay))
            _must(code, output, '准备中转站')
        code, output = self._call('remote', 'remove', 'relay')
        code, output = self._call('remote', 'add', 'relay', str(relay))
        _must(code, output, '记住中转站')
        return {'relay': str(relay)}

    # ---- 存档 / 时间线 / 现状 ----

    def save_snapshot(self, summary):
        """存档：把当前 Mod 的样子记一笔。没变化就不记。"""
        summary = str(summary or '').strip() or '随手存档'
        if len(summary) > SUMMARY_MAX:
            raise SyncError(f'存档说明最多 {SUMMARY_MAX} 个字。', 'summary_too_long')
        code, output = self._call('status', '--porcelain=v1', '-uall')
        _must(code, output, '看现状')
        if not output.strip():
            return {'saved': False, 'note': '和上次存档一模一样，这次不用记。'}
        code, output = self._call('add', '-A')
        _must(code, output, '存档')
        device = ensure_device(self.store)
        message = f'{summary}\n\n{device["name"]} · {_localnow()}'
        code, output = self._call('commit', '-m', message)
        if code != 0 and 'nothing to commit' in output.lower():
            return {'saved': False, 'note': '和上次存档一模一样，这次不用记。'}
        _must(code, output, '存档')
        code, output = self._call('rev-parse', '--short', 'HEAD')
        _must(code, output, '存档')
        return {'saved': True, 'id': output.strip()}

    def timeline(self, limit=50):
        """时间线：倒着翻以前的存档点。"""
        code, output = self._call('log', f'--max-count={int(limit)}',
                                  '--format=%H%x1f%ad%x1f%an%x1f%s', '--date=iso')
        if code != 0:
            return []
        entries = []
        for line in output.splitlines():
            parts = line.split('\x1f')
            if len(parts) != 4:
                continue
            full, when, who, what = parts
            entries.append({'编号': full[:8], '时间': when, '作者': who,
                            '说明': what.splitlines()[0] if what.strip() else '随手存档'})
        return entries

    def current_state(self):
        """现状：相对上次存档，改了什么、新增什么、删了什么。"""
        if not self.ready():
            return {'已修改': [], '新增': [], '已删除': []}
        code, output = self._call('status', '--porcelain=v1', '-uall')
        _must(code, output, '看现状')
        changed, added, removed = [], [], []
        for line in output.splitlines():
            if len(line) < 4:
                continue
            left, path = line[:2], line[3:].strip().strip('"')
            if left == '??':
                added.append(path)
            elif 'D' in left and left.strip(' ') == 'D':
                removed.append(path)
            elif 'D' in left:
                removed.append(path)
            else:
                changed.append(path)
        return {'已修改': changed, '新增': added, '已删除': removed}

    # ---- 加入 / 同步 / 合稿 ----

    def join(self, relay_dir, display_name=None):
        """加入：中转站里已有稿，拿下来开工。要求 Mod 文件夹是空的。"""
        if self._head():
            raise SyncError('这台电脑已经加入过了，直接点同步就行。', 'already_joined')
        if any(self.mod.iterdir()):
            raise SyncError('这个文件夹里已有东西，请选空文件夹加入，或先把里面的东西备份走。',
                            'join_not_empty')
        self.start_fresh(display_name)
        relay = Path(relay_dir).resolve()
        code, output = self._call('remote', 'remove', 'relay')
        code, output = self._call('remote', 'add', 'relay', str(relay))
        _must(code, output, '记住中转站')
        code, output = self._call('fetch', 'relay')
        _must(code, output, '去中转站拿稿')
        code, output = self._call('rev-parse', '--verify', f'relay/{MAIN_LINE}')
        if code != 0:
            raise SyncError('中转站里还没有稿，请让先发的人先同步一次。', 'relay_empty')
        code, output = self._call('checkout', '-B', MAIN_LINE, f'relay/{MAIN_LINE}')
        _must(code, output, '拿稿')
        if read_team(self.mod) is not None:
            device = ensure_device(self.store)
            add_member(self.mod, device)
            self.commit_team(f'新成员加入：{device["name"]}')
        return {'ok': True}

    def commit_team(self, message):
        """只提交协作组文件（成员变动），不动别的。没变化就不记。"""
        device = ensure_device(self.store)
        code, output = self._call('status', '--porcelain=v1', '--', TEAM_FILE)
        _must(code, output, '协作设置')
        if not output.strip():
            return {'committed': False}
        code, _ = self._call('add', TEAM_FILE)
        _must(code, '', '协作设置')
        code, _ = self._call('commit', '--no-edit', '-m',
                             f'{message}\n\n{device["name"]} · {_localnow()}')
        _must(code, '', '协作设置')
        return {'committed': True}

    def _relay_branch_exists(self):
        code, _ = self._call('rev-parse', '--verify', f'relay/{MAIN_LINE}')
        return code == 0

    def sync(self, progress=None):
        """同步：收别人的、合到一起、再发上去。全程中文结果。"""
        progress = progress or SyncProgress()
        try:
            self.start_fresh()
            code, _ = self._call('remote', 'get-url', 'relay')
            if code != 0:
                raise SyncError('还没选中转站，请先选一个网盘文件夹当中转站。', 'relay_missing')
            progress.set('正在收中转站的稿', 10)
            progress.check()
            code, output = self._call('fetch', 'relay')
            if code != 0:
                raise SyncError('中转站还没同步完（网盘正在传），稍后再点同步。', 'relay_not_ready')
            report = {'合上': [], '待选': [], '已发送': False, '说明': []}
            if not self._relay_branch_exists():
                if not self._head():
                    report['说明'].append('还没有存档，先存一版再同步。')
                    return report
                progress.set('正在发上去', 80)
                progress.check()
                pushed = self._push()
                report['已发送'] = pushed
                report['说明'].append('中转站是空的，已把你的稿放上去。')
                progress.set('同步完成', 100)
                return report
            before = self._head()
            progress.set('正在合稿', 40)
            progress.check()
            code, output = self._call('merge', '--no-commit', '--no-ff', f'relay/{MAIN_LINE}')
            if code == 0:
                self._finish_merge('同步：收下中转站的更新')
                report['合上'] = self._changed_between(before, self._head())
                if not report['合上']:
                    report['说明'].append('两边一模一样，不用合。')
            else:
                report['待选'] = self._keep_both()
                self._finish_merge('合稿：两边都改了，各留一份待选')
                report['合上'] = self._changed_between(before, self._head())
                report['说明'].append('有几处两边改的不一样，都留着，请在待选里挑。')
            progress.set('正在发上去', 80)
            progress.check()
            report['已发送'] = self._push_with_retry(progress)
            progress.set('同步完成', 100)
            return report
        except _Cancelled:
            return {'合上': [], '待选': self.pending_picks(), '已发送': False,
                    '说明': ['已停止等待；若内容已发出，可去中转站确认，本地稿子都在。'],
                    'cancelled': True}

    def _head(self):
        code, output = self._call('rev-parse', 'HEAD')
        return output.strip() if code == 0 else ''

    def _changed_between(self, before, after):
        if not before or not after or before == after:
            return []
        code, output = self._call('diff', '--name-only', before, after)
        if code != 0:
            return []
        return [line.strip() for line in output.splitlines() if line.strip()]

    def _finish_merge(self, message):
        device = ensure_device(self.store)
        code, output = self._call('add', '-A')
        _must(code, output, '合稿')
        code, output = self._call('commit', '--no-edit', '-m',
                                  f'{message}\n\n{device["name"]} · {_localnow()}')
        if code != 0 and 'nothing to commit' not in output.lower():
            _must(code, output, '合稿')

    def _keep_both(self):
        """两边都改了：留我的，另存对方的，记一笔待选。"""
        code, output = self._call('diff', '--name-only', '--diff-filter=U')
        _must(code, output, '看合稿情况')
        picked = []
        device = ensure_device(self.store)
        target_dir = self.store / PICK_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        for path in [line.strip() for line in output.splitlines() if line.strip()]:
            mine = self._blob(f':2:{path}')
            theirs = self._blob(f':3:{path}')
            note = {'文件': path}
            if mine is None and theirs is not None:
                # 我删了、对方改了：改的赢，捡回来。
                (self.mod / path).parent.mkdir(parents=True, exist_ok=True)
                (self.mod / path).write_bytes(theirs)
                note['处理'] = '你删过、对方改过，已按对方的捡回来。'
            elif theirs is None and mine is not None:
                code, _ = self._call('checkout', '--ours', '--', path)
                _must(code, '', '合稿')
                note['处理'] = '对方删过、你改过，已保留你的。'
            else:
                code, _ = self._call('checkout', '--ours', '--', path)
                _must(code, '', '合稿')
                aside = target_dir / self._aside_name(path, device)
                aside.write_bytes(theirs if theirs is not None else b'')
                note['处理'] = '两边不一样，已保留你的，对方的存在待选里。'
                note['对方版本'] = str(aside)
            picked.append(note)
        fresh = {item['文件'] for item in picked}
        kept = [item for item in self.pending_picks() if item.get('文件') not in fresh]
        self._write_picks(kept + [
            {'文件': item['文件'], '对方版本': item.get('对方版本', ''),
             '处理': item['处理'], '作者': device.get('name', '对方'), '时间': _localnow()}
            for item in picked])
        return picked

    def _picks_path(self):
        return self.store / PICKS_FILE

    def _write_picks(self, picks):
        try:
            self._picks_path().write_text(json.dumps(picks, ensure_ascii=False, indent=2),
                                          encoding='utf-8')
        except OSError:
            pass

    def pending_picks(self):
        """还没定稿的待选（对方版本文件还在才算数）。"""
        try:
            data = json.loads(self._picks_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        alive = []
        changed = False
        for item in data:
            if not isinstance(item, dict) or not item.get('文件'):
                changed = True
                continue
            aside = item.get('对方版本')
            if aside and not Path(aside).is_file():
                changed = True
                continue
            alive.append(item)
        if changed:
            self._write_picks(alive)
        return alive

    def resolve_pick(self, filename, choice):
        """给一份待选定稿：choice 为 留我的 / 要对方的。"""
        if choice not in ('留我的', '要对方的'):
            raise SyncError('请选择“留我的”或“要对方的”。', 'pick_invalid')
        picks = self.pending_picks()
        entry = next((item for item in picks if item.get('文件') == filename), None)
        if entry is None:
            raise SyncError('这份待选已经不在了，请刷新。', 'pick_missing')
        device = ensure_device(self.store)
        if choice == '要对方的':
            aside = Path(entry.get('对方版本', ''))
            try:
                inside = aside.resolve().parent == (self.store / PICK_DIR).resolve()
            except OSError:
                inside = False
            if not aside.is_file() or not inside:
                raise SyncError('对方版本找不到了，请重新同步一次。', 'pick_missing')
            target = self.mod / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(aside.read_bytes())
            code, _ = self._call('add', '-A')
            _must(code, '', '合稿定稿')
            code, _ = self._call('commit', '--no-edit', '-m',
                                 f'合稿定稿：{filename}用{entry.get("作者", "对方")}的版\n\n'
                                 f'{device["name"]} · {_localnow()}')
            _must(code, '', '合稿定稿')
            aside.unlink(missing_ok=True)
        remaining = [item for item in picks if item.get('文件') != filename]
        self._write_picks(remaining)
        return {'ok': True, '剩余待选': len(remaining)}

    def _blob(self, ref):
        code, output = self._call('show', ref, binary=True)
        return None if code != 0 else output

    @staticmethod
    def _aside_name(path, device):
        stem = Path(path).stem
        suffix = Path(path).suffix
        author = _sanitize(device.get('name', '对方'))
        return f'{stem}.{author}的版{suffix}'

    def _push(self):
        code, output = self._call('push', 'relay', f'{MAIN_LINE}:{MAIN_LINE}')
        return code == 0

    def _push_with_retry(self, progress=None):
        if self._push():
            return True
        code, output = self._call('fetch', 'relay')
        if code != 0:
            raise SyncError('中转站还没同步完（网盘正在传），稍后再点同步。', 'relay_not_ready')
        before = self._head()
        code, output = self._call('merge', '--no-commit', '--no-ff', f'relay/{MAIN_LINE}')
        if code != 0:
            self._keep_both()
        self._finish_merge('合稿：和中转站对齐后重发')
        _ = before
        if progress is not None:
            progress.check()
        if not self._push():
            raise SyncError('对方也在同步，先收了他的再发一次即可。', 'push_retry')
        return True

    # ---- 定稿 / 回退 / 设备 / 中转站信息 ----

    def snapshot_count(self):
        """时间线里共有几个存档点（拿不到就报 0，不报错）。"""
        code, output = self._call('rev-list', '--count', 'HEAD')
        if code != 0:
            return 0
        try:
            return max(0, int(output.strip()))
        except ValueError:
            return 0

    def set_device_name(self, name):
        """给这台电脑改名。"""
        name = str(name or '').strip()
        if not name or len(name) > DEVICE_NAME_MAX:
            raise SyncError(f'设备名字 1 到 {DEVICE_NAME_MAX} 个字。', 'device_name_invalid')
        device = ensure_device(self.store, name)
        code, _ = self._call('config', 'user.name', device['name'])
        return device

    def relay_info(self):
        """中转站地址＋参与过的人（按最近一次出现排序）。"""
        code, output = self._call('remote', 'get-url', 'relay')
        relay = output.strip() if code == 0 else ''
        seen, devices = set(), []
        for entry in self.timeline(limit=200):
            who = entry.get('作者', '')
            if not who or who in seen:
                continue
            seen.add(who)
            devices.append({'名字': who, '最近': entry.get('时间', '')})
        return {'中转站': relay, '成员': devices}

    def restore_snapshot(self, commit_id):
        """回到某次存档：先自动存下当前样子，再恢复，保证丢不了。"""
        short = str(commit_id or '').strip()
        if not re.fullmatch(r'[0-9a-fA-F]{7,40}', short):
            raise SyncError('这个存档编号不对，请重新选。', 'restore_invalid')
        code, output = self._call('rev-parse', '--verify', short)
        if code != 0:
            raise SyncError('找不到这次存档，可能已经被整理掉，请刷新时间线。', 'restore_missing')
        full = output.strip()
        message = self.timeline(limit=200)
        label = next((entry['说明'] for entry in message if entry['编号'] == full[:8]), '旧存档')
        state = self.current_state()
        if state['已修改'] or state['新增'] or state['已删除']:
            self.save_snapshot('回退前自动存档')
        code, output = self._call('checkout', full, '--', '.')
        _must(code, output, '回到存档')
        device = ensure_device(self.store)
        code, _ = self._call('add', '-A')
        _must(code, '', '回到存档')
        code, _ = self._call('commit', '--no-edit', '-m',
                             f'回到存档：{label}\n\n{device["name"]} · {_localnow()}')
        _must(code, '', '回到存档')
        code, output = self._call('rev-parse', '--short', 'HEAD')
        return {'ok': True, 'id': output.strip() if code == 0 else ''}

    # ---- 体量提醒 ----

    def size_warning(self):
        """Mod 太大先提醒一句（只看内容文件，不算备份和临时）。"""
        total = 0
        for base, directories, names in __import__('os').walk(self.mod, followlinks=False):
            base = Path(base)
            directories[:] = [name for name in directories
                              if name not in {'Backups'} and not (base / name).is_symlink()]
            for name in names:
                path = base / name
                if path.is_symlink() or name in {'.save.lock'} or path.suffix.lower() in {'.tmp', '.log'}:
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        if total > LARGE_WARN_BYTES:
            return f'这个 Mod 约 {total // 1024 // 1024}MB，同步会比较久，请耐心等。'
        return ''
