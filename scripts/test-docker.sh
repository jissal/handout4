#!/bin/sh
# Run on a machine with Linux containers, Python dev dependencies and a Korean font.
set -eu
cd "$(dirname "$0")/.."
result_dir="$(pwd)/test-results/docker512"
mkdir -p "$result_dir"
container="handout4-verify-$$"
cleanup() {
  docker inspect "$container" > "$result_dir/container-inspect.json" 2>/dev/null || true
  docker logs "$container" > "$result_dir/server.log" 2>&1 || true
  docker exec "$container" cat /sys/fs/cgroup/memory.peak > "$result_dir/kernel-peak-bytes.txt" 2>/dev/null || true
  docker exec "$container" cat /sys/fs/cgroup/memory.events > "$result_dir/memory-events.txt" 2>/dev/null || true
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
python tests/make_samples.py
python tests/make_large_samples.py
docker build -t handout4:verification .
docker run -d --name "$container" --memory=512m --memory-swap=512m --pids-limit=64 \
  -p 127.0.0.1:18000:10000 handout4:verification
python -c 'import time,urllib.request; ready=False
for _ in range(60):
 try:
  urllib.request.urlopen("http://127.0.0.1:18000/healthz",timeout=2); ready=True; break
 except OSError: time.sleep(1)
assert ready, "Container did not become ready"'
python scripts/benchmark.py http://127.0.0.1:18000 test-results/sample-12.pdf test-results/sample-12.pptx \
  test-results/large-inputs/images-25MiB.pdf test-results/large-inputs/images-25MiB.pptx \
  test-results/large-inputs/images-50MiB.pdf test-results/large-inputs/images-50MiB.pptx --out "$result_dir"
python scripts/check_runtime.py http://127.0.0.1:18000
docker exec "$container" python -c 'import psutil; from pathlib import Path; assert not list(Path("/tmp/handout4-jobs").iterdir()); assert not [p for p in psutil.process_iter() if p.name().startswith(("soffice","gpg"))]'
