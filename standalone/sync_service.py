"""同步任务服务：给编辑器界面用的薄封装。

耗时同步跑在后台线程里，界面轮询任务状态；短操作直接同步返回。
所有对外形状沿用 sync_store 的中文键，本模块只负责：排队、进度、
取消、中转站位置检查、前后版本对照。
"""
import secrets
import threading
import time
from pathlib import Path

from sync_store import ModSync, SyncError, SyncProgress, ensure_device

JOB_KEEP = 8

_STATUS = {'conflict': 409, 'join_not_empty': 409, 'already_joined': 409,
           'relay_empty': 409, 'push_retry': 409, 'pick_missing': 404,
           'restore_missing': 404, 'relay_forbidden': 403}


def _api_errors(fn):
    """SyncError 在此统一转成界面认识的 ApiError；ApiError 原样通过。"""
    def wrapper(self, store, api, *args):
        try:
            return fn(self, store, api, *args)
        except SyncError as error:
            raise api.ApiError(error.message, _STATUS.get(error.code, 400), error.code)
    wrapper.__name__ = fn.__name__
    return wrapper


def _forbidden_relay(store, project, relay):
    """中转站不能放在 Mod、订阅目录或游戏里，否则账本会被传上工坊。"""
    try:
        resolved = Path(relay).resolve()
    except OSError:
        return '这个路径打不开，请换一个。'
    roots = []
    try:
        roots.append(Path(project.path).resolve())
    except OSError:
        pass
    try:
        from game_locator import is_workshop_path
        if is_workshop_path(resolved, store.workshop):
            return '中转站不能放在订阅目录里，请换网盘里的其他文件夹。'
    except Exception:
        pass
    try:
        game = Path(store.game).resolve()
        if resolved == game or game in resolved.parents:
            return '中转站不能放在游戏目录里，请换网盘里的其他文件夹。'
    except OSError:
        pass
    for root in roots:
        if resolved == root or root in resolved.parents:
            return '中转站不能放在 Mod 文件夹里（账本会被一起传走），请换网盘里的其他文件夹。'
    return ''


class SyncJob:
    def __init__(self, job_id, project_id):
        self.id = job_id
        self.project_id = project_id
        self.progress = SyncProgress()
        self.state = 'queued'
        self.report = None
        self.error = None
        self.revision = ''
        self.changed_during = False
        self.created = time.time()

    def public(self):
        body = {'jobId': self.id, 'projectId': self.project_id, 'state': self.state,
                'phase': self.progress.phase, 'percent': self.progress.percent}
        if self.report is not None:
            body['report'] = self.report
        if self.error is not None:
            body['error'] = self.error
        if self.revision:
            body['revision'] = self.revision
        return body


class SyncService:
    def __init__(self):
        self._lock = threading.RLock()
        self._jobs = {}

    def _register(self, job):
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > JOB_KEEP:
                self._jobs.pop(next(iter(self._jobs)))

    def status(self, job_id):
        with self._lock:
            job = self._jobs.get(str(job_id or ''))
            return job.public() if job else None

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(str(job_id or ''))
            if job is None:
                return None
            job.progress.cancelled.set()
            if job.state in ('queued', 'running'):
                job.state = 'cancelling'
                job.progress.set('正在停下来', job.progress.percent)
            return job.public()

    def close(self):
        """切换目录时调用：停掉所有任务，旧目录的同步不再继续。"""
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs = {}
        for job in jobs:
            job.progress.cancelled.set()
            if job.state in ('queued', 'running', 'cancelling'):
                job.state = 'cancelled'

    def _syncer(self, store, project):
        return ModSync(project.path, None)

    def start(self, store, project_id):
        project = store.project(project_id, writable=True)
        job = SyncJob(secrets.token_hex(8), project.id)
        job.revision = store.revision(project)
        self._register(job)
        worker = threading.Thread(target=self._run, args=(store, job, project),
                                  daemon=True, name='mod-sync')
        worker.start()
        return job.public()

    def _run(self, store, job, project):
        job.state = 'running'
        syncer = self._syncer(store, project)
        before = job.revision
        try:
            report = syncer.sync(job.progress)
            job.report = report
            if job.progress.cancelled.is_set():
                job.state = 'cancelled'
                job.progress.set('已停止', job.progress.percent)
            else:
                job.state = 'done'
                job.progress.set('同步完成', 100)
            try:
                job.revision = store.revision(project)
                job.changed_during = (before != job.revision)
                if job.changed_during and isinstance(job.report, dict):
                    notes = list(job.report.get('说明', []))
                    notes.append('同步时 Mod 有过保存，已一并同步。')
                    job.report['说明'] = notes
            except Exception:
                pass
        except SyncError as error:
            job.state = 'error'
            job.error = {'message': error.message, 'code': error.code}
        except Exception as error:
            job.state = 'error'
            job.error = {'message': f'同步出了点问题：{error}，本地稿子都在，请稍后再试。',
                         'code': 'internal_error'}

    # ---- short operations (called directly by routes) ----

    @_api_errors
    def save(self, store, api, payload):
        import save_review
        project = store.project((payload or {}).get('projectId'), writable=True)
        revision = store.revision(project)
        save_review.revision(payload, revision, api.ApiError, '同步前模组已经变化，请刷新后重试。')
        syncer = self._syncer(store, project)
        syncer.start_fresh()
        result = syncer.save_snapshot((payload or {}).get('summary', ''))
        result['revision'] = store.revision(project)
        return result

    @_api_errors
    def set_relay(self, store, api, payload):
        project = store.project((payload or {}).get('projectId'), writable=True)
        raw = ((payload or {}).get('path') or '').strip()
        if not raw:
            raise SyncError('请选择网盘里的一个空文件夹当中转站。', 'relay_missing')
        relay = Path(raw).expanduser()
        if not relay.is_absolute():
            raise SyncError('请填写中转站文件夹的完整路径。', 'relay_invalid')
        complaint = _forbidden_relay(store, project, relay)
        if complaint:
            raise SyncError(complaint, 'relay_forbidden')
        syncer = self._syncer(store, project)
        syncer.start_fresh()
        return syncer.set_relay(relay)

    @_api_errors
    def join(self, store, api, payload):
        project = store.project((payload or {}).get('projectId'), writable=True)
        raw = ((payload or {}).get('relay') or '').strip()
        if not raw:
            raise SyncError('请先选好中转站文件夹。', 'relay_missing')
        relay = Path(raw).expanduser()
        if not relay.is_absolute() or not relay.is_dir():
            raise SyncError('中转站文件夹打不开，请检查网盘是否已同步下来。', 'relay_unreachable')
        syncer = self._syncer(store, project)
        name = ((payload or {}).get('deviceName') or '').strip()
        result = syncer.join(relay, name or None)
        result['revision'] = store.revision(project)
        return result

    @_api_errors
    def resolve_pick(self, store, api, payload):
        project = store.project((payload or {}).get('projectId'), writable=True)
        syncer = self._syncer(store, project)
        result = syncer.resolve_pick(((payload or {}).get('file') or ''),
                                     ((payload or {}).get('choice') or ''))
        result['revision'] = store.revision(project)
        return result

    @_api_errors
    def restore(self, store, api, payload):
        project = store.project((payload or {}).get('projectId'), writable=True)
        syncer = self._syncer(store, project)
        result = syncer.restore_snapshot(((payload or {}).get('id') or ''))
        result['revision'] = store.revision(project)
        return result

    @_api_errors
    def rename_device(self, store, api, payload):
        project = store.project((payload or {}).get('projectId'), writable=True)
        syncer = self._syncer(store, project)
        syncer.start_fresh()
        return {'device': syncer.set_device_name(((payload or {}).get('name') or ''))}

    @_api_errors
    def overview(self, store, api, project_id):
        project = store.project(project_id)
        syncer = self._syncer(store, project)
        device = ensure_device(syncer.store)
        state = syncer.current_state()
        info = syncer.relay_info()
        return {
            'projectId': project.id,
            'joined': bool(info['中转站']),
            'relay': info['中转站'],
            'members': info['成员'],
            'device': {'id': device.get('id', ''), 'name': device.get('name', '')},
            'pendingPicks': syncer.pending_picks(),
            'timeline': syncer.timeline(limit=30),
            'snapshotCount': syncer.snapshot_count(),
            'state': {'已修改': len(state['已修改']), '新增': len(state['新增']),
                      '已删除': len(state['已删除'])},
            'sizeWarning': syncer.size_warning(),
            'revision': store.revision(project),
            'readOnly': bool(getattr(project, 'readOnly', False) or getattr(project, 'readonly', False)
                             or str(project.id).startswith('workshop:')),
        }
