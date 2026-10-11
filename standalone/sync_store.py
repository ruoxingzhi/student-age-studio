"""多人协作与多平台同步的地基层：借用 git 存历史，对用户 полностью 中文。

用户永远看不到 git：存档、时间线、同步、合稿单、待选、平行世界。
git 只是一个"账本"，装在本机、中转站里，不进 Mod，不进游戏，不上传工坊。
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

MAIN_LINE = 'main'
DEVICE_FILE = 'device.json'
PICK_DIR = '合稿待选'
QUEUE_DIR = '待发送'
LARGE_WARN_BYTES = 900 * 1024 * 1024
DEFAULT_TIMEOUT = 300

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
        return {'ok': True}

    def _relay_branch_exists(self):
        code, _ = self._call('rev-parse', '--verify', f'relay/{MAIN_LINE}')
        return code == 0

    def sync(self):
        """同步：收别人的、合到一起、再发上去。全程中文结果。"""
        self.start_fresh()
        code, _ = self._call('remote', 'get-url', 'relay')
        if code != 0:
            raise SyncError('还没选中转站，请先选一个网盘文件夹当中转站。', 'relay_missing')
        code, output = self._call('fetch', 'relay')
        if code != 0:
            raise SyncError('中转站还没同步完（网盘正在传），稍后再点同步。', 'relay_not_ready')
        report = {'合上': [], '待选': [], '已发送': False, '说明': []}
        if not self._relay_branch_exists():
            if not self._head():
                report['说明'].append('还没有存档，先存一版再同步。')
                return report
            pushed = self._push()
            report['已发送'] = pushed
            report['说明'].append('中转站是空的，已把你的稿放上去。')
            return report
        before = self._head()
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
        report['已发送'] = self._push_with_retry()
        return report

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
        return picked

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

    def _push_with_retry(self):
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
        if not self._push():
            raise SyncError('对方也在同步，先收了他的再发一次即可。', 'push_retry')
        return True

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
