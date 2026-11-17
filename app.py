from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
from urllib.parse import quote
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
import psutil

from conversion import libreoffice_path

BASE = Path(__file__).resolve().parent
MAX_UPLOAD = 50 * 1024 * 1024
JOB_TIMEOUT = float(os.getenv('JOB_TIMEOUT_SECONDS', '120'))
TOTAL_MEMORY_MB = float(os.getenv('TOTAL_MEMORY_MB', '448'))
UPLOAD_TIMEOUT = 120
TEMP_ROOT = Path(os.getenv('JOB_TEMP_ROOT', tempfile.gettempdir())) / 'handout4-jobs'
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
gate = asyncio.Lock()
log = logging.getLogger('uvicorn.error')


def cgroup_memory() -> tuple[int, int] | None:
    """Render/Docker cgroup v2, or v1. Do not confuse host RAM with container RAM."""
    for used, maximum in [('/sys/fs/cgroup/memory.current', '/sys/fs/cgroup/memory.max'),
                          ('/sys/fs/cgroup/memory/memory.usage_in_bytes', '/sys/fs/cgroup/memory/memory.limit_in_bytes')]:
        try:
            limit = int(Path(maximum).read_text().strip())
            if 0 < limit < 16 * 1024 ** 3:
                return int(Path(used).read_text().strip()), limit
        except (OSError, ValueError):
            continue
    return None


def remove_job(directory: Path):
    if directory.resolve().parent != TEMP_ROOT.resolve() or not directory.name.startswith('job-'):
        raise RuntimeError('Temporary job path is outside the expected directory')
    shutil.rmtree(directory, ignore_errors=True)


@asynccontextmanager
async def lifespan(app):
    # A crashed process cannot run finally. Remove abandoned jobs on restart.
    # Run exactly one server process; never share JOB_TEMP_ROOT between services.
    for directory in TEMP_ROOT.glob('job-*'):
        if directory.is_dir() and not directory.is_symlink():
            remove_job(directory)
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class ResponseHeaders:
    """Pure ASGI middleware preserves http.disconnect for the conversion supervisor."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_with_headers(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [*message.get('headers', []),
                    (b'cache-control', b'no-store'),
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'),
                    (b'content-security-policy',
                     b"default-src 'self'; script-src 'self'; worker-src 'self' blob:; "
                     b"style-src 'self'; img-src 'self' blob: data:; font-src 'self' blob:; "
                     b"connect-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")]
            await send(message)
        await self.app(scope, receive, send_with_headers)


app.add_middleware(ResponseHeaders)


@app.exception_handler(HTTPException)
async def readable_error(request: Request, exc: HTTPException):
    return JSONResponse({'error': exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get('/')
async def home():
    return FileResponse(BASE / 'static/index.html')


@app.get('/healthz')
async def health():
    # Readiness also checks the engine. No synthetic keep-alive traffic is generated.
    return {'status': 'ok', 'pptx': bool(libreoffice_path()), 'busy': gate.locked()}


def kill_tree(pid: int):
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    else:
        for proc in reversed(children):
            with contextlib.suppress(psutil.Error):
                proc.kill()
        with contextlib.suppress(psutil.Error):
            parent.kill()
    if os.name != 'nt':
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
    psutil.wait_procs(children, timeout=2)


async def supervise(directory: Path, kind: str, request: Request):
    # Keep graphic swap files, GPG probes and every per-user cache inside the job.
    for folder in ('home', 'cache', 'config', 'gpg'):
        (directory / folder).mkdir(mode=0o700)
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(BASE / 'conversion.py'), str(directory), kind,
        cwd=BASE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=os.name != 'nt',
        env={**os.environ, 'HOME':str(directory / 'home'), 'TMPDIR':str(directory),
             'TMP':str(directory), 'TEMP':str(directory), 'GNUPGHOME':str(directory / 'gpg'),
             'XDG_CACHE_HOME':str(directory / 'cache'), 'XDG_CONFIG_HOME':str(directory / 'config'),
             'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
             'SAL_USE_VCLPLUGIN': 'svp', 'MALLOC_ARENA_MAX': '2'})
    started, peak, app_peak, cgroup_peak = time.monotonic(), 0, 0, 0
    waiting = asyncio.create_task(process.wait())
    # Remember descendants even if the wrapper process exits before cleanup.
    descendants: dict[int, psutil.Process] = {}
    try:
        while not waiting.done():
            if time.monotonic() - started > JOB_TIMEOUT:
                raise HTTPException(422, '변환 시간이 초과되었습니다. 원본의 이미지를 줄이거나 PDF로 저장해 올려 주세요.')
            if await request.is_disconnected():
                raise HTTPException(499, '연결이 끊겨 변환을 취소했습니다.')
            rss = 0
            try:
                root = psutil.Process(process.pid)
                for proc in [root, *root.children(recursive=True)]:
                    if proc.pid != root.pid:
                        descendants[proc.pid] = proc
                    with contextlib.suppress(psutil.Error):
                        rss += proc.memory_info().rss
            except psutil.NoSuchProcess:
                pass
            peak = max(peak, rss)
            app_rss = psutil.Process().memory_info().rss + rss
            app_peak = max(app_peak, app_rss)
            container = cgroup_memory()
            if container:
                cgroup_peak = max(cgroup_peak, container[0])
            container_full = container is not None and container[0] > min(
                container[1] - 64 * 1024 ** 2, TOTAL_MEMORY_MB * 1024 ** 2)
            # cgroup is the authoritative whole-container budget on Linux. Summed RSS
            # counts shared pages repeatedly, so use it only when no cgroup is available.
            memory_full = container_full if container is not None else app_rss > TOTAL_MEMORY_MB * 1024 ** 2
            if memory_full:
                log.warning('conversion_memory_limit kind=%s job_rss_mb=%.2f app_and_job_rss_mb=%.2f cgroup_mb=%s',
                            kind, rss / 1024 ** 2, app_rss / 1024 ** 2,
                            round(container[0] / 1024 ** 2, 2) if container else None)
                raise HTTPException(422, '무료 서버의 메모리 한도에 가까워 변환을 중단했습니다. 원본 앱에서 PDF로 저장한 뒤 올려 주세요.',
                    headers={'X-Peak-Job-Rss-Mb':str(round(peak / 1024 ** 2, 2)),
                             'X-Peak-App-And-Job-Rss-Mb':str(round(app_peak / 1024 ** 2, 2)),
                             'X-Peak-Cgroup-Mb':str(round(cgroup_peak / 1024 ** 2, 2))})
            await asyncio.wait([waiting], timeout=0.05)
        await waiting
        status = directory / 'status.json'
        if process.returncode != 0 or not status.exists():
            raise HTTPException(422, '변환 프로세스가 종료되었습니다. 파일 크기를 줄이거나 PDF로 저장해 다시 시도해 주세요.')
        data = json.loads(status.read_text(encoding='utf-8'))
        if 'error' in data:
            raise HTTPException(422, data['error'])
        data.update(peak_job_rss_mb=round(peak / 1024 ** 2, 2),
                    peak_app_and_job_rss_mb=round(app_peak / 1024 ** 2, 2),
                    peak_cgroup_mb=round(cgroup_peak / 1024 ** 2, 2) if cgroup_peak else None)
        log.info('conversion kind=%s slides=%d seconds=%s job_rss_mb=%s app_and_job_rss_mb=%s cgroup_mb=%s',
                 kind, data['slides'], data['seconds'], data['peak_job_rss_mb'],
                 data['peak_app_and_job_rss_mb'], data['peak_cgroup_mb'])
        return data
    finally:
        await asyncio.to_thread(kill_tree, process.pid)
        for proc in descendants.values():
            with contextlib.suppress(psutil.Error):
                if proc.is_running():
                    proc.kill()
        await process.wait()
        if not waiting.done():
            waiting.cancel()


@app.post('/api/convert')
async def convert(request: Request, filename: str = ''):
    # Raw streaming body avoids multipart duplication and parser spool files.
    safe_name = filename.replace('\\', '/').split('/')[-1]
    kind = Path(safe_name).suffix.lower().lstrip('.')
    if kind not in {'pdf', 'pptx'}:
        raise HTTPException(415, 'PDF 또는 PPTX 파일을 선택해 주세요.')
    length = request.headers.get('content-length')
    if length and (not length.isdigit() or int(length) > MAX_UPLOAD):
        raise HTTPException(413, '파일은 50MiB까지 올릴 수 있습니다. 원본의 이미지 크기를 줄여 주세요.')
    if gate.locked():
        raise HTTPException(429, '다른 파일을 변환하고 있어요. 잠시 후 다시 올려 주세요.', headers={'Retry-After': '10'})
    await gate.acquire()
    try:
        directory = Path(tempfile.mkdtemp(prefix='job-', dir=TEMP_ROOT))
    except OSError:
        gate.release()
        raise HTTPException(503, '서버의 임시 공간을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.') from None
    cleaned = False

    def cleanup():
        nonlocal cleaned
        if not cleaned:
            cleaned = True
            remove_job(directory)
            gate.release()

    try:
        total = 0
        try:
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                with (directory / f'input.{kind}').open('wb') as target:
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > MAX_UPLOAD:
                            raise HTTPException(413, '파일은 50MiB까지 올릴 수 있습니다.')
                        target.write(chunk)
        except TimeoutError:
            raise HTTPException(408, '파일 업로드 시간이 초과되었습니다. 인터넷 연결을 확인해 주세요.') from None
        if not total:
            raise HTTPException(400, '빈 파일입니다. 다른 파일을 선택해 주세요.')
        data = await supervise(directory, kind, request)
        result = directory / 'result.pdf'
        headers = {'X-Slide-Count': str(data['slides']), 'X-Page-Count': str(data['pages']),
                   'X-Conversion-Seconds': str(data['seconds']),
                   'X-Peak-Job-Rss-Mb': str(data['peak_job_rss_mb']),
                   'X-Peak-App-And-Job-Rss-Mb': str(data['peak_app_and_job_rss_mb']),
                   'Content-Disposition': f"attachment; filename*=UTF-8''{quote(Path(safe_name).stem + '.pdf')}",
                   'Content-Length': str(result.stat().st_size)}
        if data['peak_cgroup_mb'] is not None:
            headers['X-Peak-Cgroup-Mb'] = str(data['peak_cgroup_mb'])

        async def stream():
            try:
                with result.open('rb') as handle:
                    while chunk := handle.read(64 * 1024):
                        yield chunk
                        await asyncio.sleep(0)
            finally:
                cleanup()

        return StreamingResponse(stream(), media_type='application/pdf', headers=headers,
                                 background=BackgroundTask(cleanup))
    except BaseException:
        cleanup()
        raise


app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
