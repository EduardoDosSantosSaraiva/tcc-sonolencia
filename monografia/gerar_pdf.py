#!/usr/bin/env python3
"""
Gera o PDF do texto no mesmo template do TCC-modelo enviado pelo orientador
(TCC_Ana_Carolina_Melo.pdf), cujas medidas foram extraídas do próprio arquivo:

    A4 (595 x 842 pt)                margens 3 cm (topo/esquerda) e 2 cm (direita/base)
    Times New Roman 12 pt            texto justificado, entrelinha 1,15
    recuo de primeira linha 1,22 cm  espaço de 10 pt entre parágrafos
    títulos em Times 12 pt negrito   caixa alta, alinhados à esquerda
    número da página no alto, à direita, em Times 12 pt

Como não há pandoc nem LibreOffice no Raspberry Pi, a impressão é feita pelo
Chromium em modo headless; a numeração das páginas é carimbada depois com
reportlab + pypdf (o Chromium não sabe numerar no canto superior direito).

Uso:
    ../venv/bin/python gerar_pdf.py                  # só a introdução
    ../venv/bin/python gerar_pdf.py --tudo           # pré-textuais + introdução
"""

import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).parent
DESKTOP = Path.home() / "Desktop"

A4 = (595.276, 841.89)
MARGEM_DIREITA_PT = 56.7          # 2 cm
NUMERO_BASELINE_PT = 795          # altura do número da página (a partir da base)

CSS = """
 @page{size:A4;margin:3cm 2cm 2cm 3cm}
 body{font-family:"Times New Roman",Times,serif;font-size:12pt;line-height:1.15;
      margin:0;color:#000;text-align:justify}
 p{text-indent:1.22cm;margin:0 0 10pt;orphans:3;widows:3}
 p.ref{text-indent:0;margin:0 0 10pt}
 h1{font-size:12pt;font-weight:bold;text-transform:uppercase;text-align:left;
    margin:0 0 18pt;page-break-after:avoid}
 h2{font-size:12pt;font-weight:bold;text-align:left;margin:18pt 0 10pt;page-break-after:avoid}
 h3{font-size:12pt;font-weight:normal;text-align:center;margin:1em 0}
 h1+h1,hr+h1{margin-top:24pt}
 sup{font-size:.7em;vertical-align:super;line-height:0}
 ul{margin:0 0 10pt 0}li{text-align:justify;margin-bottom:.3em}
 code{font-family:"Courier New",monospace;font-size:11pt}
 a{color:#000;text-decoration:none}
 .capa{text-align:center}
 .capa h1,.capa h2{text-align:center;margin:1.6em 0;page-break-after:auto}
 .capa p{text-indent:0}
 .pagina{page-break-after:always}
 .pagina:last-child{page-break-after:auto}
"""


def corpo(caminho):
    """Conteúdo do <body> sem as anotações de revisão nem a sugestão de título."""
    miolo = re.search(r"<body[^>]*>(.*)</body>",
                      Path(caminho).read_text(), re.S).group(1)
    corte = miolo.find("<h2>Observações para revisão")
    if corte != -1:
        anterior = miolo.rfind("<hr>", 0, corte)
        miolo = miolo[:anterior if anterior != -1 else corte]
    miolo = re.sub(r'<div class="nota">\s*<p>\s*<strong>Título sugerido.*?</div>',
                   "", miolo, flags=re.S)
    return miolo.strip()


def numerar(pdf, primeira=1):
    """Carimba o número no alto à direita, como no TCC-modelo."""
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
    except ImportError:
        print("  (pypdf/reportlab ausentes: PDF gerado sem numeração)")
        return

    leitor = PdfReader(pdf)
    escritor = PdfWriter()
    for i, pagina in enumerate(leitor.pages):
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.setFont("Times-Roman", 12)
        c.drawRightString(A4[0] - MARGEM_DIREITA_PT, NUMERO_BASELINE_PT, str(primeira + i))
        c.save()
        buffer.seek(0)
        pagina.merge_page(PdfReader(buffer).pages[0])
        escritor.add_page(pagina)
    with open(pdf, "wb") as f:
        escritor.write(f)


def main():
    tudo = "--tudo" in sys.argv
    capitulos = (["00_pre_textual.html", "01_introducao.html"] if tudo
                 else ["01_introducao.html"])
    saida = AQUI / ("TCC - texto parcial.pdf" if tudo else "TCC - Introducao.pdf")

    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        print("Chromium não encontrado — não dá pra gerar o PDF.")
        sys.exit(1)

    partes = []
    for nome in capitulos:
        arquivo = AQUI / nome
        if not arquivo.exists():
            print(f"Faltando {nome}: rode antes 'python3 gerar_html.py'")
            sys.exit(1)
        capa = nome.startswith("00_")
        for pagina in re.split(r"<hr>", corpo(arquivo)):
            if not pagina.strip():
                continue
            # nas folhas pré-textuais cada bloco é uma página; no texto corrido,
            # só as referências começam em página nova
            classe = "pagina capa" if capa else "pagina"
            partes.append(f'<div class="{classe}">{pagina}</div>')

    documento = ('<!doctype html>\n<html lang="pt-BR"><head><meta charset="utf-8">'
                 "<title>TCC</title>\n"
                 f"<style>{CSS}</style></head><body>\n"
                 + "\n".join(partes) + "\n</body></html>\n")

    with tempfile.TemporaryDirectory() as tmp:
        fonte = Path(tmp) / "tcc.html"
        fonte.write_text(documento)
        subprocess.run(
            [chromium, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-pdf-header-footer", f"--print-to-pdf={saida}", fonte.as_uri()],
            check=True, capture_output=True, timeout=180)

    numerar(saida)
    print(f"PDF gerado: {saida}")
    if DESKTOP.is_dir():
        shutil.copy(saida, DESKTOP / saida.name)
        print(f"Área de Trabalho: {saida.name}")


if __name__ == "__main__":
    main()
