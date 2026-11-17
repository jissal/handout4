"""Lossless page placement. This module runs only inside a bounded worker process."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile
from xml.etree import ElementTree as ET

A4_WIDTH = 297 / 25.4 * 72
A4_HEIGHT = 210 / 25.4 * 72
MAX_SLIDES = 120
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


class ConversionError(Exception):
    pass


def libreoffice_path() -> str | None:
    return os.getenv('SOFFICE_PATH') or shutil.which('libreoffice') or shutil.which('soffice')


def check_picture_header(archive: zipfile.ZipFile, entry: zipfile.ZipInfo):
    """Read PNG/JPEG dimensions without decoding pixels or copying image payloads."""
    if not entry.filename.startswith('ppt/media/'):
        return
    with archive.open(entry) as picture:
        header = picture.read(24)
        dimensions = None
        if header.startswith(b'\x89PNG\r\n\x1a\n') and len(header) == 24:
            dimensions = (int.from_bytes(header[16:20],'big'),int.from_bytes(header[20:24],'big'))
        elif header.startswith(b'\xff\xd8'):
            picture.seek(2)
            while picture.tell() < 256 * 1024:
                byte = picture.read(1)
                if not byte: break
                if byte != b'\xff': continue
                marker = picture.read(1)
                while marker == b'\xff': marker = picture.read(1)
                if marker in (b'\xd9',b'\xda',b''): break
                if marker == b'\x00' or marker[0] in [0x01,*range(0xD0,0xD9)]: continue
                length = int.from_bytes(picture.read(2),'big')
                if length < 2: break
                if marker[0] in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF):
                    size = picture.read(5)
                    if len(size) == 5:
                        dimensions = (int.from_bytes(size[3:5],'big'),int.from_bytes(size[1:3],'big'))
                    break
                picture.read(length - 2)
        if dimensions and dimensions[0] * dimensions[1] > MAX_IMAGE_PIXELS:
            raise ConversionError('PPTX에 2천만 화소를 넘는 이미지가 있습니다. 이미지 해상도를 줄이거나 원본 앱에서 PDF로 저장해 주세요.')


def validate_pptx(path: Path) -> int:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(i.file_size for i in entries) > 100 * 1024 * 1024:
                raise ConversionError('압축을 푼 파일이 너무 큽니다. 이미지 크기를 줄이거나 PDF로 저장해 올려 주세요.')
            names = set(archive.namelist())
            if '[Content_Types].xml' not in names or 'ppt/presentation.xml' not in names:
                raise ConversionError('올바른 PPTX 파일이 아닙니다. PowerPoint에서 다시 저장해 주세요.')
            if any('vbaProject' in n for n in names):
                raise ConversionError('매크로가 포함된 파일은 사용할 수 없습니다. 일반 PPTX로 저장해 주세요.')
            if archive.getinfo('ppt/presentation.xml').file_size > 2 * 1024 ** 2:
                raise ConversionError('PPTX의 슬라이드 정보가 너무 큽니다. 원본 앱에서 다시 저장해 주세요.')
            raw = archive.read('ppt/presentation.xml')
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                raise ConversionError('안전하게 읽을 수 없는 PPTX 파일입니다.')
            root = ET.fromstring(raw)
            count = len(root.findall('./{http://schemas.openxmlformats.org/presentationml/2006/main}sldIdLst/*'))
            if not 1 <= count <= MAX_SLIDES:
                raise ConversionError(f'슬라이드는 1~{MAX_SLIDES}개까지 변환할 수 있습니다.')
            # Do not let Impress download externally linked pictures, media, or documents.
            # Ordinary hyperlinks remain valid and are not followed during rendering.
            for entry in entries:
                check_picture_header(archive,entry)
                if not entry.filename.endswith('.rels'):
                    continue
                if entry.file_size > 2 * 1024 ** 2:
                    raise ConversionError('PPTX의 연결 정보가 너무 큽니다. 원본 앱에서 다시 저장해 주세요.')
                data = archive.read(entry)
                if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
                    raise ConversionError('안전하게 읽을 수 없는 PPTX 파일입니다.')
                for rel in ET.fromstring(data):
                    if rel.get('TargetMode') == 'External' and not rel.get('Type', '').endswith('/hyperlink'):
                        raise ConversionError('외부에 연결된 이미지나 미디어가 있습니다. 파일에 포함해 저장하거나 PDF로 올려 주세요.')
            return count
    except ConversionError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError, NotImplementedError, OSError):
        raise ConversionError('손상되었거나 암호가 걸린 PPTX입니다. 파일을 다시 저장해 주세요.') from None


def pptx_to_pdf(source: Path, directory: Path) -> tuple[Path, int, float]:
    expected = validate_pptx(source)
    engine = libreoffice_path()
    if not engine:
        raise ConversionError('서버의 PPTX 변환 엔진이 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.')
    profile = directory / 'lo-profile'
    (profile / 'user').mkdir(parents=True)
    (profile / 'user/registrymodifications.xcu').write_text('''<?xml version="1.0"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Cache/GraphicManager"><prop oor:name="GraphicMemoryLimit" oor:op="fuse"><value>16777216</value></prop><prop oor:name="GraphicSwappingEnabled" oor:op="fuse"><value>true</value></prop><prop oor:name="GraphicAllowedIdleTime" oor:op="fuse"><value>1</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Cache/DrawingEngine"><prop oor:name="OLE_Objects" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Cache/Font"><prop oor:name="GlyphsCacheSize" oor:op="fuse"><value>4194304</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/VCL"><prop oor:name="PDFExportImageCacheSize" oor:op="fuse"><value>1</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/VCL"><prop oor:name="UseSkia" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="UseOpenCL" oor:op="fuse"><value>false</value></prop></item>
</oor:items>''', encoding='utf-8')
    started = time.monotonic()
    command = [engine, f'-env:UserInstallation={profile.resolve().as_uri()}', '--headless', '--nologo',
               '--nodefault', '--nolockcheck', '--norestore', '--convert-to',
               'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}',
               '--outdir', str(directory), str(source)]
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=100, check=False)
    except subprocess.TimeoutExpired:
        # The outer supervisor kills this worker and all surviving descendants as well.
        raise ConversionError('PPTX 변환 시간이 초과되었습니다. 이미지를 줄이거나 원본 앱에서 PDF로 저장해 주세요.') from None
    output = source.with_suffix('.pdf')
    if result.returncode != 0 or not output.is_file() or output.stat().st_size < 8:
        raise ConversionError('PPTX를 읽을 수 없습니다. PowerPoint에서 파일이 열리는지 확인하고 다시 저장해 주세요.')
    return output, expected, time.monotonic() - started


def arrange_pdf(source: Path, destination: Path, expected: int | None = None) -> int:
    # The API process and the worker's LibreOffice phase do not need pypdf in RAM.
    from pypdf import PdfReader, PdfWriter, Transformation
    try:
        reader = PdfReader(source, strict=False)
        if reader.is_encrypted:
            raise ConversionError('암호가 걸린 PDF입니다. 암호를 해제한 파일을 올려 주세요.')
        count = len(reader.pages)
        if not 1 <= count <= MAX_SLIDES:
            raise ConversionError(f'원본은 1~{MAX_SLIDES}페이지까지 변환할 수 있습니다.')
        if expected is not None and count != expected:
            raise ConversionError('PPTX의 슬라이드 수와 변환 결과가 다릅니다. 원본 앱에서 PDF로 저장한 뒤 올려 주세요.')
        writer = PdfWriter()
        margin, gap = 18.0, 12.0
        cell_w = (A4_WIDTH - margin * 2 - gap) / 2
        cell_h = (A4_HEIGHT - margin * 2 - gap) / 2
        for index, page in enumerate(reader.pages):
            if index % 4 == 0:
                target = writer.add_blank_page(width=A4_WIDTH, height=A4_HEIGHT)
            page.transfer_rotation_to_content()
            box = page.cropbox
            x0, y0 = float(box.left), float(box.bottom)
            width, height = float(box.width), float(box.height)
            if not all(math.isfinite(v) for v in (x0, y0, width, height)) or min(width, height) <= 0:
                raise ConversionError('페이지 크기를 읽을 수 없는 PDF입니다. 원본 앱에서 다시 저장해 주세요.')
            scale = min(cell_w / width, cell_h / height)
            col, row = index % 2, (index % 4) // 2
            left = margin + col * (cell_w + gap) + (cell_w - width * scale) / 2
            bottom = A4_HEIGHT - margin - (row + 1) * cell_h - row * gap + (cell_h - height * scale) / 2
            matrix = Transformation().translate(-x0, -y0).scale(scale).translate(left, bottom)
            # Keep vector objects, embedded fonts and original crop clipping. No rasterization.
            target.merge_transformed_page(page, matrix, over=True)
        writer.add_metadata({'/Title': 'A4 4-slide handout', '/Producer': 'Handout4 / pypdf'})
        writer.write(destination)
        if destination.stat().st_size > MAX_OUTPUT_BYTES:
            raise ConversionError('결과 PDF는 64MiB까지 만들 수 있습니다. 원본의 이미지 크기를 줄여 주세요.')
        return count
    except ConversionError:
        raise
    except Exception:
        raise ConversionError('PDF를 읽거나 배치할 수 없습니다. 원본 앱에서 PDF로 다시 저장해 주세요.') from None


def run_job(directory: Path, kind: str) -> dict:
    started = time.monotonic()
    source = directory / f'input.{kind}'
    expected, lo_seconds = None, None
    if kind == 'pptx':
        source, expected, lo_seconds = pptx_to_pdf(source, directory)
    else:
        with source.open('rb') as header:
            if header.read(5) != b'%PDF-':
                raise ConversionError('올바른 PDF 파일이 아닙니다. 확장자만 바꾼 파일은 변환할 수 없습니다.')
    count = arrange_pdf(source, directory / 'result.pdf', expected)
    return {'slides': count, 'pages': math.ceil(count / 4),
            'seconds': round(time.monotonic() - started, 3),
            'libreoffice_seconds': round(lo_seconds, 3) if lo_seconds is not None else None}


if __name__ == '__main__':
    directory, kind = Path(sys.argv[1]), sys.argv[2]
    try:
        data = run_job(directory, kind)
    except ConversionError as exc:
        data = {'error': str(exc)}
    except Exception:
        data = {'error': '변환 중 문제가 생겼습니다. 파일을 다시 저장한 후 시도해 주세요.'}
    (directory / 'status.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
