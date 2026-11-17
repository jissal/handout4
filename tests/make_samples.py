"""Deterministic test fixtures. Boxes and pixel patterns are test targets, not artwork."""
from pathlib import Path
from io import BytesIO

from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

OUT = Path(__file__).resolve().parents[1] / 'test-results'


def make_samples():
    OUT.mkdir(exist_ok=True)
    image = Image.new('RGB', (320, 180), '#2f7554')
    # Distinct quadrants allow pixel-level detection of clipping and distortion.
    for x in range(320):
        for y in range(180):
            image.putpixel((x, y), [(240, 90, 70), (245, 190, 60), (70, 145, 210), (70, 170, 120)][(x >= 160) + 2 * (y >= 90)])
    image.save(OUT / 'image.png')
    font_paths = [Path('C:/Windows/Fonts/malgun.ttf'), Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf')]
    font_path = next((p for p in font_paths if p.exists()), None)
    if font_path is None:
        raise RuntimeError('Korean font needed to generate fixtures')
    pdfmetrics.registerFont(TTFont('Korean', str(font_path)))
    for count in (12, 5):
        pdf = canvas.Canvas(str(OUT / f'sample-{count}.pdf'), pagesize=(960, 540))
        deck = Presentation()
        deck.slide_width, deck.slide_height = Inches(13.333333), Inches(7.5)
        for i in range(1, count + 1):
            pdf.setFillColorRGB(.97, .98, .95)
            pdf.rect(0, 0, 960, 540, fill=1, stroke=0)
            pdf.setFillColorRGB(.09, .3, .2)
            pdf.setFont('Helvetica-Bold', 60)
            pdf.drawString(48, 430, f'SLIDE {i:02}')
            pdf.setFont('Korean', 30)
            pdf.drawString(48, 360, '제주에서 발견하는 살아 있는 국어')
            pdf.setFont('Korean', 22)
            pdf.drawString(48, 310, '한국어 글꼴과 이미지 출력 확인')
            pdf.drawImage(ImageReader(image), 48, 60, width=320, height=180)
            pdf.setStrokeColorRGB(.15, .3, .2)
            pdf.rect(12, 12, 936, 516, fill=0, stroke=1)
            pdf.showPage()
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor(247, 250, 242)
            for text, top, size in [(f'SLIDE {i:02}', .65, 48),
                                    ('제주에서 발견하는 살아 있는 국어', 2.1, 29),
                                    ('한국어 글꼴과 이미지 출력 확인', 2.9, 22)]:
                box = slide.shapes.add_textbox(Inches(.65), Inches(top), Inches(12), Inches(.8))
                paragraph = box.text_frame.paragraphs[0]
                paragraph.text = text
                paragraph.font.name = 'Malgun Gothic'
                paragraph.font.size = Pt(size)
                paragraph.font.color.rgb = RGBColor(25, 80, 55)
            slide.shapes.add_picture(str(OUT / 'image.png'), Inches(.65), Inches(4.3), width=Inches(4.444444))
        pdf.save()
        deck.save(OUT / f'sample-{count}.pptx')
    # Portrait, rotated, cropped with non-zero origin, and square pages.
    special = PdfWriter()
    for width, height, rotation in [(320, 640, 0), (960, 540, 90), (960, 540, 0), (540, 540, 0)]:
        stream = BytesIO()
        c = canvas.Canvas(stream, pagesize=(width, height))
        c.setFont('Helvetica', 22)
        c.drawCentredString(width / 2, height / 2, f'{width}x{height}')
        c.drawImage(ImageReader(image), width / 4, height / 4, width=width / 2, height=height / 2)
        c.showPage(); c.save()
        page = PdfReader(stream).pages[0]
        if len(special.pages) == 2:
            page.cropbox = RectangleObject((80, 40, 880, 500))
        if rotation: page.rotate(rotation)
        special.add_page(page)
    special.write(OUT / 'mixed-ratios.pdf')
    protected = PdfWriter()
    protected.append(OUT / 'sample-5.pdf')
    protected.encrypt('password')
    protected.write(OUT / 'encrypted.pdf')
    (OUT / 'broken.pdf').write_bytes(b'%PDF-1.7\nnot a real PDF')
    (OUT / 'broken.pptx').write_bytes(b'not a zip')
    return OUT


if __name__ == '__main__':
    print(make_samples())
