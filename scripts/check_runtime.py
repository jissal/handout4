"""Manual real-HTTP checks: concurrency, cancelled request cleanup and chunked limits."""
import argparse
import asyncio
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('url', help='http://127.0.0.1:8000 or a deployed URL')
args = parser.parse_args()


async def main():
    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        source = ROOT / 'test-results/sample-12.pptx'
        task = asyncio.create_task(client.post('/api/convert?filename=cancel-test.pptx', content=source.read_bytes()))
        for _ in range(100):
            if (await client.get('/healthz')).json()['busy']:
                break
            await asyncio.sleep(.05)
        else:
            raise AssertionError('Conversion never started')
        other = await client.post('/api/convert?filename=second.pdf', content=b'%PDF-1.7 invalid')
        assert other.status_code == 429, other.text
        await asyncio.sleep(.1)
        assert not task.done(), 'Use a larger PPTX to test cancellation before completion'
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for _ in range(100):
            if not (await client.get('/healthz')).json()['busy']:
                break
            await asyncio.sleep(.05)
        else:
            raise AssertionError('Lock did not release after client disconnected')

        async def oversized():
            for _ in range(51):
                yield b'x' * 1024 ** 2

        huge = await client.post('/api/convert?filename=huge.pdf', content=oversized())
        assert huge.status_code == 413, huge.text
        health = (await client.get('/healthz')).json()
        assert not health['busy']
        report = {'concurrent_second_request':other.status_code,
                  'cancelled_request_released_lock':True,
                  'oversized_chunked_request':huge.status_code, 'health':health,'url':args.url}
        print(json.dumps(report, indent=2))
        (ROOT / 'test-results/runtime-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


asyncio.run(main())
