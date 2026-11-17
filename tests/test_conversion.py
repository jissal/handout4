from io import BytesIO
from pathlib import Path
import math
import sys

import fitz
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app import app, TEMP_ROOT
from conversion import A4_WIDTH, A4_HEIGHT, libreoffice_path
from make_samples import make_samples


@pytest.fixture(scope='session')
def samples():
    return make_samples()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    assert list(TEMP_ROOT.iterdir()) == [], 'server temporary files must be removed'


def convert(client, path):
    return client.post('/api/convert', params={'filename':path.name}, content=path.read_bytes(),
                       headers={'Content-Type':'application/octet-stream'})


@pytest.mark.parametrize('kind', ['pdf', 'pptx'])
@pytest.mark.parametrize('count', [12, 5])
def test_real_conversion(client, samples, kind, count):
    if kind == 'pptx' and not libreoffice_path():
        pytest.fail('PPTX engine is required; do not silently skip this test')
    response = convert(client, samples / f'sample-{count}.{kind}')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    assert int(response.headers['x-slide-count']) == count
    assert int(response.headers['x-page-count']) == math.ceil(count / 4)
    result = samples / f'result-{count}-{kind}.pdf'
    result.write_bytes(response.content)
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == math.ceil(count / 4)
    doc = fitz.open(stream=response.content, filetype='pdf')
    for pi, page in enumerate(doc):
        assert page.rect.width == pytest.approx(A4_WIDTH, abs=.02)
        assert page.rect.height == pytest.approx(A4_HEIGHT, abs=.02)
        for slot in range(min(4, count - pi * 4)):
            number = pi * 4 + slot + 1
            hits = page.search_for(f'SLIDE {number:02}')
            assert len(hits) == 1
            box = hits[0]
            assert (box.x0 < page.rect.width / 2) == (slot % 2 == 0)
            assert (box.y0 < page.rect.height / 2) == (slot < 2)
        assert '제주에서 발견하는 살아 있는 국어' in page.get_text()
        assert len(page.get_images()) >= 1
        page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).save(samples / f'result-{count}-{kind}-{pi + 1}.png')
    metrics = {key:value for key,value in response.headers.items() if key.startswith('x-')}
    (samples / f'metrics-{count}-{kind}.json').write_text(__import__('json').dumps(metrics, indent=2), encoding='utf-8')
    assert not list(TEMP_ROOT.iterdir())


def test_ratios_rotation_crop(client, samples):
    response = convert(client, samples / 'mixed-ratios.pdf')
    assert response.status_code == 200, response.text
    source = fitz.open(samples / 'mixed-ratios.pdf')
    target = fitz.open(stream=response.content, filetype='pdf')
    # Compare actual rasterized source against each centered cell at the same scale.
    from PIL import Image, ImageChops, ImageStat
    page = target[0]
    cw, ch = (A4_WIDTH - 48) / 2, (A4_HEIGHT - 48) / 2
    for i, original in enumerate(source):
        scale = min(cw / original.rect.width, ch / original.rect.height)
        rendered = original.get_pixmap(matrix=fitz.Matrix(scale * 2, scale * 2), alpha=False)
        x = 18 + (i % 2) * (cw + 12) + (cw - original.rect.width * scale) / 2
        y = 18 + (i // 2) * (ch + 12) + (ch - original.rect.height * scale) / 2
        clip = fitz.Rect(x, y, x + original.rect.width * scale, y + original.rect.height * scale)
        actual = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
        expected_img = Image.frombytes('RGB', (rendered.width, rendered.height), rendered.samples)
        actual_img = Image.frombytes('RGB', (actual.width, actual.height), actual.samples).resize(expected_img.size)
        diff = ImageStat.Stat(ImageChops.difference(expected_img, actual_img))
        assert max(diff.mean) < 7, (i, diff.mean)
    page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).save(samples / 'mixed-ratios-result.png')


@pytest.mark.parametrize('file,status', [('broken.pdf',422),('broken.pptx',422),('encrypted.pdf',422)])
def test_invalid(client, samples, file, status):
    response = convert(client, samples / file)
    assert response.status_code == status
    assert response.json()['error']


def test_upload_limits(client):
    assert client.post('/api/convert?filename=x.exe', content=b'x').status_code == 415
    assert client.post('/api/convert?filename=x.pdf', content=b'').status_code == 400
    assert client.post('/api/convert?filename=x.pdf', content=b'x', headers={'content-length':str(50*1024**2+1)}).status_code == 413


def test_busy(client):
    import app as module
    class Busy:
        def locked(self): return True
    original = module.gate
    module.gate = Busy()
    try:
        response = client.post('/api/convert?filename=x.pdf', content=b'x')
        assert response.status_code == 429
        assert response.headers['retry-after'] == '10'
    finally:
        module.gate = original


def test_memory_guard(client, samples, monkeypatch):
    monkeypatch.setattr('app.TOTAL_MEMORY_MB', .01)
    response = convert(client, samples / 'sample-12.pptx')
    assert response.status_code == 422
    assert '메모리' in response.json()['error']


def test_time_guard(client, samples, monkeypatch):
    monkeypatch.setattr('app.JOB_TIMEOUT', .001)
    response = convert(client, samples / 'sample-12.pptx')
    assert response.status_code == 422
    assert '시간' in response.json()['error']


def test_site_assets(client):
    for url in ['/', '/static/app.js', '/static/style.css', '/static/vendor/build/pdf.mjs', '/static/vendor/build/pdf.worker.mjs']:
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
    assert client.get('/healthz').json()['status'] == 'ok'


def test_cgroup_total_budget(client, samples, monkeypatch):
    monkeypatch.setattr('app.cgroup_memory',lambda:(449*1024**2,512*1024**2))
    response = convert(client,samples/'sample-12.pptx')
    assert response.status_code == 422
    assert '메모리' in response.json()['error']
    assert float(response.headers['x-peak-cgroup-mb']) == 449


def test_pptx_expansion_limit(client, tmp_path):
    import zipfile
    path = tmp_path/'compressed.pptx'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open('ppt/media/payload','w') as member:
            for _ in range(101): member.write(b'a'*1024**2)
    response=convert(client,path)
    assert response.status_code == 422
    assert '압축' in response.json()['error']


def test_oversized_picture_header(client, samples, tmp_path):
    import zipfile
    path=tmp_path/'oversized-picture.pptx'
    with zipfile.ZipFile(samples/'sample-12.pptx') as source,zipfile.ZipFile(path,'w') as target:
        for entry in source.infolist():
            data=source.read(entry)
            if entry.filename.endswith('image1.png'):
                data=data[:16]+(10000).to_bytes(4,'big')+(10000).to_bytes(4,'big')+data[24:]
            target.writestr(entry,data)
    response=convert(client,path)
    assert response.status_code == 422
    assert '화소' in response.json()['error']
