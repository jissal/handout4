"""Run on a deployed URL as well as localhost. Uses real files; writes local evidence only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from pypdf import PdfReader

parser = argparse.ArgumentParser(description='배포 서버에 실제 PDF/PPTX를 보내 페이지 수와 자원 측정값 확인')
parser.add_argument('url', help='예: https://handout4-xxxx.onrender.com')
parser.add_argument('files', nargs='+', type=Path)
parser.add_argument('--out', type=Path, default=Path('test-results/deployed'))
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
records, failed = [], False
for path in args.files:
    started = time.monotonic()
    url = args.url.rstrip('/') + '/api/convert?' + urllib.parse.urlencode({'filename':path.name})
    request = urllib.request.Request(url, data=path.read_bytes(), headers={'Content-Type':'application/octet-stream'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = response.read()
            headers = dict(response.headers)
            output = args.out / f'{path.stem}-{path.suffix[1:]}-handout.pdf'
            output.write_bytes(data)
            reader = PdfReader(output)
            record = {'file':path.name, 'status':response.status, 'headers':headers,
                      'output_pages':len(reader.pages), 'sha256':hashlib.sha256(data).hexdigest()}
            lower = {k.lower():v for k,v in headers.items()}
            slides = int(lower['x-slide-count'])
            assert len(reader.pages) == (slides + 3) // 4
            assert all(abs(float(p.mediabox.width) - 841.89) < .1 and abs(float(p.mediabox.height) - 595.28) < .1 for p in reader.pages)
    except urllib.error.HTTPError as exc:
        record = {'file':path.name, 'status':exc.code, 'error':exc.read().decode('utf-8', errors='replace')}
        failed = True
    except Exception as exc:
        record = {'file':path.name, 'error':str(exc)}
        failed = True
    record['round_trip_seconds'] = round(time.monotonic() - started, 3)
    records.append(record)
    print(json.dumps(record, ensure_ascii=False, indent=2))
(args.out / 'report.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
sys.exit(1 if failed else 0)
