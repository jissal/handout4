# PDF·PPTX 유인물 변환기

PDF 또는 PPTX를 올리면 A4 가로 용지에 2×2로 배치합니다. 순서는 왼쪽 위 → 오른쪽 위 → 왼쪽 아래 → 오른쪽 아래입니다. 12슬라이드는 3페이지가 됩니다.

파일 업로드, 실제 PDF 미리보기·다운로드, 짧은 사용방법만 표시합니다. PDF.js로 페이지 이동·확대·축소·화면 맞춤을 제공하며, 파일명을 바꿔 같은 PDF 데이터를 다운로드합니다. 12슬라이드가 아니어도 경고 후 변환합니다. 로그인·DB·유료 API는 없습니다.

PDF는 원본 페이지를 벡터로 재배치하며 회전·CropBox·비율을 반영합니다. PPTX는 실제 LibreOffice Impress로 PDF를 만든 후 재배치합니다. Nanum 한글 글꼴을 포함합니다. 원본에 사용한 모든 글꼴이나 PowerPoint 효과를 동일하게 재현한다고 보장하지는 않으므로 제출 전 미리보기를 확인하세요.

## 실행

### Docker / Linux

```sh
docker build -t handout4 .
docker run --rm --memory=512m --memory-swap=512m --pids-limit=64 \
  -p 10000:10000 handout4
```

`http://localhost:10000`으로 접속합니다. Dockerfile의 tini가 LibreOffice의 고아 프로세스를 회수하므로 시작 명령을 따로 덮어쓰지 마세요. Python 서버는 반드시 worker 1개로 실행합니다.

### Windows

Python 3.12+, Node.js 24+, LibreOffice를 설치하고 실행합니다.

```powershell
.\start-local.ps1
```

`http://127.0.0.1:8000`으로 접속합니다. LibreOffice 경로가 다르면 `SOFFICE_PATH`에 `soffice.com`의 경로를 지정하세요. Windows 로컬 실행은 메모리 특성이 달라 `TOTAL_MEMORY_MB=768`을 사용합니다. Render의 448MiB 안전 한도와 Linux Docker의 512MiB 물리 한도는 변경하지 않습니다.

## 안전 제한

| 항목 | 제한 |
| --- | --- |
| 입력 PDF / PPTX | 각각 최대 50MiB (52,428,800바이트) |
| 결과 PDF | 최대 64MiB |
| PPTX 압축 해제 합계 / 항목 수 | 100MiB / 5,000개 |
| PPTX 제어 XML | 파일당 2MiB |
| PPTX의 PNG·JPEG | 이미지당 최대 2,000만 픽셀 |
| 슬라이드 수 | 최대 120개 |
| 전체 변환 / LibreOffice | 120초 / 100초 |
| 업로드 수신 | 120초 |
| 컨테이너 전체 메모리 안전 한도 | 448MiB; cgroup 물리 한도에서 64MiB 여유 유지 |
| 동시 변환 | 1건 (추가 요청은 한국어 안내와 429 응답) |

50MiB는 입력 허용치입니다. 압축률·이미지 해상도·도형 수에 따라 작은 파일도 메모리나 시간 제한에 걸릴 수 있습니다. 사진 해상도를 줄이거나 원본 도구에서 PDF로 내보내세요. PNG/JPEG 이외의 이미지도 전체 메모리·시간 제한을 적용합니다.

업로드는 디스크로 스트리밍하고 결과는 64KiB 단위로 전송합니다. 서버에서는 PDF를 이미지로 렌더링하지 않습니다. 변환·실패·시간 초과·연결 취소 후 프로세스와 작업 폴더를 정리합니다. 결과 전송이 끝나면 서버 파일을 삭제하며, 미리보기와 다운로드는 브라우저의 동일한 Blob을 사용합니다. 영구 디스크는 사용하지 않습니다.

## Render 무료 배포

저장소: https://github.com/jissal/handout4

Render에서 New → Web Service → 저장소 연결을 선택합니다.

- Branch: `main`
- Runtime: Docker
- Region: Singapore
- Instance Type: Free
- Dockerfile: `./Dockerfile`, Context: `.`
- Docker Command: 비워 둠 (tini와 Dockerfile 시작 명령 유지)
- `TOTAL_MEMORY_MB=448`, `JOB_TIMEOUT_SECONDS=120`, `PORT=10000`
- HTTP Health Check Path: `/healthz` (기본 TCP 검사도 지원)

또는 `render.yaml`을 Blueprint로 배포할 수 있습니다. 비밀번호나 API 키를 코드에 넣지 않습니다. 무료 서비스는 유휴 상태에서 절전하며 첫 접속·PPTX 요청에 대기 시간이 생깁니다. 화면에 안내하며 외부 절전 방지 호출은 사용하지 않습니다. 무료 512MB/0.1CPU의 Docker 시험은 통과했지만 실제 Render 실행 여부는 배포 후 별도로 확인해야 합니다.

## 검증

실제 시험 수치와 제약은 [OPTIMIZATION_REPORT.md](OPTIMIZATION_REPORT.md)에 있습니다. Docker 시험과 Render 배포 시험은 구분합니다. 과거 `TEST_REPORT.md`·`UI_REPORT.md`는 초기 Windows 및 UI 검증 기록입니다.

```sh
python -m pip install -r requirements-dev.txt
npm ci --omit=dev
npm run vendor
python tests/make_samples.py
python -m pytest -q
python tests/make_large_samples.py
python scripts/benchmark.py http://localhost:10000 \
  test-results/sample-12.pdf test-results/sample-12.pptx \
  test-results/large-inputs/images-50MiB.pdf test-results/large-inputs/images-50MiB.pptx
python scripts/check_runtime.py http://localhost:10000
```

Linux Docker 시험 절차를 `scripts/test-docker.sh`에도 정리했습니다. 대용량 샘플은 실제 고유 이미지 12개를 포함합니다. `exact-50MiB` 파일은 그 이미지 기반 파일의 메타데이터를 채운 업로드 경계 시험용입니다. 입력/결과 파일은 GitHub에 올리지 않으며, 서버 배포에는 개발용 렌더링 라이브러리를 설치하지 않습니다.
