"""Create image-backed inputs, not padded files, for memory/size measurements."""
from pathlib import Path
from io import BytesIO
import json
import random
import sys

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt
from reportlab import rl_config
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'test-results/large-inputs'
OUT.mkdir(parents=True, exist_ok=True)
font_paths = [Path('C:/Windows/Fonts/malgun.ttf'), Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf')]
pdfmetrics.registerFont(TTFont('Korean', next(p for p in font_paths if p.exists()).as_posix()))
rl_config.useA85 = False  # Normal binary JPEG streams; avoid artificial ASCII85 size inflation.
manifest = []
for mib in (25, 50):
    folder = OUT / f'images-{mib}'
    folder.mkdir(exist_ok=True)
    target = (mib - .6) * 1024 ** 2 / 12
    images = []
    for index in range(12):
        image = Image.frombytes('RGB', (2560, 1440), random.Random(100 + index).randbytes(2560 * 1440 * 3))
        # Colored corner targets expose wrong scaling, clipping or missing pictures.
        for box, color in [((0,0,160,160),'red'),((2400,0,2560,160),'blue'),
                           ((0,1280,160,1440),'green'),((2400,1280,2560,1440),'yellow')]:
            image.paste(color, box)
        low, high = 1, 100
        best = None
        while low <= high:
            quality = (low + high) // 2
            data = BytesIO()
            image.save(data, 'JPEG', quality=quality, subsampling=0)
            content = data.getvalue()
            if len(content) <= target:
                best = content
                low = quality + 1
            else:
                high = quality - 1
        path = folder / f'image-{index + 1:02}.jpg'
        path.write_bytes(best)
        images.append(path)
    pdf_path = OUT / f'images-{mib}MiB.pdf'
    pptx_path = OUT / f'images-{mib}MiB.pptx'
    pdf = canvas.Canvas(str(pdf_path), pagesize=(960,540))
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333333), Inches(7.5)
    for index, image in enumerate(images, 1):
        pdf.setFont('Helvetica-Bold',24)
        pdf.drawString(30,502,f'SLIDE {index:02}')
        pdf.setFont('Korean',18)
        pdf.drawString(200,502,'한국어 이미지 보존 확인')
        pdf.drawImage(ImageReader(str(image)),30,15,width=900,height=470,preserveAspectRatio=True,anchor='c')
        pdf.showPage()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for text, left, size, font in [(f'SLIDE {index:02}',.42,24,'Arial'),('한국어 이미지 보존 확인',2.8,18,'Malgun Gothic')]:
            box = slide.shapes.add_textbox(Inches(left), Inches(.15), Inches(8), Inches(.55))
            paragraph = box.text_frame.paragraphs[0]
            paragraph.text = text
            paragraph.font.name = font
            paragraph.font.size = Pt(size)
            paragraph.font.color.rgb = RGBColor(0,0,0)
        slide.shapes.add_picture(str(image), Inches(.42), Inches(.9), width=Inches(11.7))
    pdf.save()
    deck.save(pptx_path)
    for path in (pdf_path, pptx_path):
        item = {'file':path.name,'bytes':path.stat().st_size,'mib':round(path.stat().st_size/1024**2,3),
                'slides':12,'unique_images':12,'image_pixels':[2560,1440]}
        assert 20 < item['mib'] <= 50
        manifest.append(item)
        print(json.dumps(item),flush=True)
(OUT / 'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

# These separate fixtures test the exact 50MiB boundary. Padding is explicit metadata;
# the performance cases above contain real image bytes without this padding.
import shutil
import zipfile
from pypdf import PdfWriter
limit=50*1024**2
writer=PdfWriter()
writer.append(OUT/'images-50MiB.pdf')
writer.add_metadata({'/BoundaryFixture':''})
boundary_pdf=OUT/'exact-50MiB.pdf'
writer.write(boundary_pdf)
writer.add_metadata({'/BoundaryFixture':'A'*(limit-boundary_pdf.stat().st_size)})
writer.write(boundary_pdf)
boundary_pptx=OUT/'exact-50MiB.pptx'
shutil.copy2(OUT/'images-50MiB.pptx',boundary_pptx)
name='customXml/boundary.xml'
extra=limit-boundary_pptx.stat().st_size-(76+2*len(name))
opening=b'<test xmlns="urn:handout4:boundary">'
ending=b'</test>'
with zipfile.ZipFile(boundary_pptx,'a') as archive:
    archive.writestr(name,opening+b'A'*(extra-len(opening)-len(ending))+ending,compress_type=zipfile.ZIP_STORED)
assert boundary_pdf.stat().st_size==boundary_pptx.stat().st_size==limit
(OUT/'boundary-manifest.json').write_text(json.dumps({'purpose':'exact upload boundary with explicit metadata padding',
    'files':[{'file':p.name,'bytes':p.stat().st_size} for p in [boundary_pdf,boundary_pptx]]},indent=2),encoding='utf-8')
