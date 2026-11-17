"""Stream real files through HTTP and validate result content. Development dependencies only."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import httpx
import fitz

parser = argparse.ArgumentParser()
parser.add_argument('url')
parser.add_argument('files', nargs='+', type=Path)
parser.add_argument('--out', type=Path, default=Path('test-results/benchmark'))
args = parser.parse_args()
args.out.mkdir(parents=True,exist_ok=True)
records = []
with httpx.Client(timeout=240) as client:
    for source in args.files:
        def chunks():
            with source.open('rb') as stream:
                while block := stream.read(256 * 1024):
                    yield block
        record = {'file':source.name, 'input_bytes':source.stat().st_size}
        started = time.monotonic()
        try:
            with client.stream('POST', args.url.rstrip('/') + '/api/convert', params={'filename':source.name},
                               content=chunks(),headers={'Content-Type':'application/octet-stream',
                                                        'Content-Length':str(source.stat().st_size)}) as response:
                record.update(status=response.status_code,headers=dict(response.headers))
                if response.status_code != 200:
                    response.read()
                    record['error'] = response.text
                else:
                    output = args.out / (source.name.replace('.', '-') + '-handout.pdf')
                    with output.open('wb') as handle:
                        for block in response.iter_bytes(64 * 1024): handle.write(block)
                    record['output_bytes'] = output.stat().st_size
                    with output.open('rb') as stream:
                        record['sha256'] = hashlib.file_digest(stream,'sha256').hexdigest()
                    doc = fitz.open(output)
                    assert len(doc) == 3
                    all_text = ''
                    fonts, dimensions = set(),set()
                    for pi, page in enumerate(doc):
                        assert abs(page.rect.width - 841.89) < .1
                        assert abs(page.rect.height - 595.28) < .1
                        all_text += page.get_text()
                        for slot in range(4):
                            hit, = page.search_for(f'SLIDE {pi * 4 + slot + 1:02}')
                            assert (hit.x0 < 420) == (slot % 2 == 0)
                            assert (hit.y0 < 297) == (slot < 2)
                        fonts.update(f[3] for f in page.get_fonts())
                        dimensions.update((im[2],im[3]) for im in page.get_images())
                    assert '한국어' in all_text or '제주' in all_text
                    assert dimensions
                    record.update(validated=True,pages=len(doc),fonts=sorted(fonts),image_dimensions=sorted(dimensions))
                    if source.name.startswith('images-'):
                        assert (2560,1440) in dimensions, 'source image resolution changed'
                    doc[0].get_pixmap(matrix=fitz.Matrix(1.1,1.1)).save(output.with_suffix('.png'))
        except Exception as exc:
            record['validation_error'] = str(exc)
        record['wall_seconds'] = round(time.monotonic()-started,3)
        records.append(record)
        print(json.dumps(record,ensure_ascii=False,indent=2),flush=True)
(args.out / 'report.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(1 if any(not r.get('validated') for r in records) else 0)
