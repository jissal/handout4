const $ = id => document.getElementById(id);
let controller, resultBlob, resultUrl, pdf, loadingTask, rendering;
let pageNumber = 1, generation = 0, renderVersion = 0, previewModule;
const maxBytes = 50 * 1024 * 1024;

async function clearResult() {
  generation++; renderVersion++;
  if (rendering) { rendering.cancel(); rendering = null; }
  if (loadingTask) { await loadingTask.destroy().catch(() => {}); loadingTask = null; }
  pdf = null; resultBlob = null;
  if (resultUrl) URL.revokeObjectURL(resultUrl);
  resultUrl = null;
  $('pdf-canvas').width = 0; $('pdf-canvas').height = 0;
  $('result').hidden = true; $('warning').hidden = true; $('preview-error').hidden = true;
}

function error(message) {
  $('error').textContent = message;
  $('error').hidden = false;
}

function busy(value) {
  $('choose').disabled = value;
  $('file').disabled = value;
  $('progress').hidden = !value;
  $('dropzone').setAttribute('aria-busy', String(value));
}

async function getPreviewModule() {
  previewModule ??= import('/static/vendor/build/pdf.mjs').then(module => {
    module.GlobalWorkerOptions.workerSrc = '/static/vendor/build/pdf.worker.mjs';
    return module;
  }).catch(err => { previewModule = undefined; throw err; });
  return previewModule;
}

async function renderPage() {
  if (!pdf) return;
  const version = ++renderVersion;
  const document = pdf;
  if (rendering) {
    rendering.cancel();
    await rendering.promise.catch(() => {});
  }
  $('prev').disabled = pageNumber <= 1;
  $('next').disabled = pageNumber >= document.numPages;
  $('page-label').textContent = `${pageNumber} / ${document.numPages}`;
  const page = await document.getPage(pageNumber);
  if (version !== renderVersion) return;
  const normal = page.getViewport({scale: 1});
  const viewerStyle = getComputedStyle($('viewer'));
  const padding = parseFloat(viewerStyle.paddingLeft) + parseFloat(viewerStyle.paddingRight);
  const fit = Math.max(0.1, ($('viewer').clientWidth - padding) / normal.width);
  const scale = $('zoom').value === 'fit' ? fit : Number($('zoom').value);
  // Limit backing pixels on high-DPI phones; only one page lives in canvas memory.
  const ratio = Math.min(window.devicePixelRatio || 1, 2, 3000 / (normal.width * scale));
  const viewport = page.getViewport({scale});
  const canvas = $('pdf-canvas');
  canvas.width = Math.floor(viewport.width * ratio);
  canvas.height = Math.floor(viewport.height * ratio);
  canvas.style.width = `${viewport.width}px`;
  canvas.style.height = `${viewport.height}px`;
  canvas.setAttribute('aria-label', `변환된 PDF ${pageNumber}페이지, 총 ${document.numPages}페이지`);
  rendering = page.render({canvasContext:canvas.getContext('2d'), viewport,
    transform:ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0]});
  try {
    await rendering.promise;
    if (version === renderVersion) {
      $('preview-status').hidden = true;
      $('preview-error').hidden = true;
    }
  } catch (err) {
    if (err.name !== 'RenderingCancelledException') throw err;
  }
}

function previewError() {
  $('preview-status').hidden = true;
  $('preview-error').textContent = '이 브라우저에서 미리보기를 표시하지 못했어요. PDF를 내려받아 확인하거나 최신 Chrome·Safari로 열어 주세요.';
  $('preview-error').hidden = false;
}

async function upload(file) {
  if (!file || controller) return;
  $('error').hidden = true;
  if (!/\.(pdf|pptx)$/i.test(file.name)) return error('PDF 또는 PPTX 파일을 선택해 주세요.');
  if (file.size > maxBytes) return error('파일은 50MiB까지 올릴 수 있어요. 원본의 이미지 크기를 줄여 주세요.');
  if (!file.size) return error('빈 파일입니다. 다른 파일을 선택해 주세요.');
  await clearResult();
  const current = generation;
  const isPptx = /\.pptx$/i.test(file.name);
  controller = new AbortController();
  busy(true);
  $('progress-title').textContent = '파일을 올리고 변환하고 있어요';
  $('progress-detail').textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)}MiB${isPptx ? ' · PPTX를 PDF로 변환한 뒤 네 장씩 배치해요.' : ' · PDF 페이지를 네 장씩 배치해요.'}`;
  $('cold-note').hidden = false;
  const delayed = setTimeout(() => {
    $('progress-title').textContent = '서버 준비 또는 파일 변환 중이에요';
  }, 15000);
  const timeout = setTimeout(() => controller?.abort('timeout'), 360000);
  try {
    const response = await fetch(`/api/convert?filename=${encodeURIComponent(file.name)}`, {
      method:'POST', body:file, headers:{'Content-Type':'application/octet-stream'}, signal:controller.signal
    });
    if (!response.ok) {
      let body;
      try { body = await response.json(); } catch { /* hosting gateway may send HTML */ }
      throw new Error(body?.error || '서버에 연결하지 못했어요. 잠시 기다린 뒤 다시 올려 주세요.');
    }
    if (!response.headers.get('content-type')?.includes('application/pdf')) throw new Error('서버가 올바른 PDF를 보내지 못했어요. 다시 시도해 주세요.');
    resultBlob = await response.blob();
    if (current !== generation) return;
    const slides = Number(response.headers.get('X-Slide-Count'));
    const pages = Number(response.headers.get('X-Page-Count'));
    resultUrl = URL.createObjectURL(resultBlob);
    $('filename').value = file.name.replace(/\.(pdf|pptx)$/i, '');
    $('result-summary').textContent = `${slides}슬라이드 → A4 가로 ${pages}페이지 · 한 페이지에 4슬라이드`;
    if (slides !== 12) {
      $('warning').textContent = `원본이 ${slides}슬라이드예요. 학교 안내는 12슬라이드(결과 3페이지) 기준이에요. 변환은 완료했으니 제출 조건을 확인해 주세요.`;
      $('warning').hidden = false;
    }
    $('result').hidden = false;
    $('choose').textContent = '다른 파일 선택';
    $('preview-status').hidden = false; $('preview-status').textContent = '실제 PDF를 불러오고 있어요…';
    pageNumber = 1; $('zoom').value = 'fit';
    // Preview and download both read this exact Blob. No second conversion or fetch.
    try {
      const module = await getPreviewModule();
      if (current !== generation) return;
      loadingTask = module.getDocument({data:new Uint8Array(await resultBlob.arrayBuffer()),
        cMapUrl:'/static/vendor/cmaps/', cMapPacked:true,
        standardFontDataUrl:'/static/vendor/standard_fonts/', wasmUrl:'/static/vendor/wasm/',
        isEvalSupported:false, enableXfa:false});
      pdf = await loadingTask.promise;
      if (pdf.numPages !== pages) throw new Error('page mismatch');
      await renderPage();
    } catch { previewError(); }
  } catch (err) {
    if (controller?.signal.aborted) {
      error(controller.signal.reason === 'timeout' ? '응답 대기 시간이 길어졌어요. 잠시 후 다시 올려 주세요.' : '변환을 취소했어요. 파일을 다시 선택할 수 있어요.');
    } else error(err.message || '연결이 끊겼어요. 인터넷 연결을 확인하고 다시 시도해 주세요.');
  } finally {
    clearTimeout(timeout); clearTimeout(delayed);
    controller = null; busy(false); $('file').value = '';
  }
}

$('choose').addEventListener('click', () => $('file').click());
$('file').addEventListener('change', event => upload(event.target.files[0]));
$('cancel').addEventListener('click', () => controller?.abort('cancel'));
['dragenter', 'dragover'].forEach(name => $('dropzone').addEventListener(name, event => {
  event.preventDefault(); if (!controller) $('dropzone').classList.add('dragging');
}));
['dragleave', 'drop'].forEach(name => $('dropzone').addEventListener(name, event => {
  event.preventDefault(); $('dropzone').classList.remove('dragging');
}));
$('dropzone').addEventListener('drop', event => {
  if (event.dataTransfer.files.length > 1) return error('한 번에 파일 하나만 올려 주세요.');
  upload(event.dataTransfer.files[0]);
});
window.addEventListener('dragover', event => event.preventDefault());
window.addEventListener('drop', event => event.preventDefault());
$('prev').addEventListener('click', () => { if (pdf && pageNumber > 1) { pageNumber--; renderPage().catch(previewError); } });
$('next').addEventListener('click', () => { if (pdf && pageNumber < pdf.numPages) { pageNumber++; renderPage().catch(previewError); } });
$('zoom').addEventListener('change', () => renderPage().catch(previewError));
$('viewer').addEventListener('keydown', event => {
  if (event.key === 'ArrowLeft') { event.preventDefault(); $('prev').click(); }
  if (event.key === 'ArrowRight') { event.preventDefault(); $('next').click(); }
});
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if ($('zoom').value === 'fit') renderPage().catch(previewError); }, 150);
});
$('download').addEventListener('click', () => {
  if (!resultUrl) return;
  const name = $('filename').value.replace(/\.pdf$/i, '').replace(/[<>:"/\\|?*\x00-\x1f]/g, '').trim().replace(/[. ]+$/g, '') || '제출자료';
  const anchor = document.createElement('a');
  anchor.href = resultUrl; anchor.download = `${name}.pdf`;
  document.body.append(anchor); anchor.click(); anchor.remove();
});
