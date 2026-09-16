#!/usr/bin/env python3
"""
Junta os capítulos já escritos num único PDF, com margens da ABNT
(3 cm à esquerda e no topo, 2 cm à direita e embaixo) e quebra de página
entre as folhas pré-textuais.

Usa o Chromium em modo headless para imprimir — não há pandoc nem
LibreOffice no Raspberry Pi.

Uso:
    python3 gerar_html.py && python3 gerar_pdf.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).parent
DESKTOP = Path.home() / "Desktop"
SAIDA = AQUI / "TCC - texto parcial.pdf"

# ordem dos arquivos no PDF final
CAPITULOS = ["00_pre_textual.html", "01_introducao.html"]

CSS_PDF = """
 @page{size:A4;margin:3cm 2cm 2cm 3cm}
 body{font-family:"Times New Roman",Times,serif;font-size:12pt;line-height:1.5;
      margin:0;color:#000;text-align:justify}
 h1{font-size:12pt;font-weight:bold;text-align:left;text-transform:uppercase;margin:1.4em 0 .8em}
 h2{font-size:12pt;font-weight:bold;text-align:left;margin:1.2em 0 .6em}
 h3{font-size:12pt;font-weight:normal;text-align:center;margin:1em 0}
 p{text-indent:1.25cm;margin:0 0 .35em;orphans:3;widows:3}
 p.ref{text-indent:0;margin:0 0 .8em;text-align:left}
 sup{font-size:.7em;vertical-align:super;line-height:0}
 ul{margin:0 0 1em 0}li{text-align:justify;margin-bottom:.4em}
 code{font-family:"Courier New",monospace;font-size:11pt}
 a{color:#000;text-decoration:none}
 .capa{text-align:center}
 .capa h1,.capa h2{text-align:center;margin:1.6em 0}
 .capa p{text-indent:0;text-align:justify}
 .nota{border-left:3px solid #999;padding:.4em 1em;margin:0 0 1.2em;color:#333}
 .nota p{text-indent:0;font-size:11pt}
 .pagina{page-break-after:always}
 .pagina:last-child{page-break-after:auto}
 h1{page-break-after:avoid}
"""


def corpo(caminho):
    """Extrai o conteúdo do <body>, descartando as anotações de revisão."""
    bruto = Path(caminho).read_text()
    miolo = re.search(r"<body[^>]*>(.*)</body>", bruto, re.S).group(1)

    # tira a seção "Observações para revisão" (anotações minhas, não são do TCC)
    corte = miolo.find("<h2>Observações para revisão")
    if corte != -1:
        anterior = miolo.rfind("<hr>", 0, corte)
        miolo = miolo[:anterior if anterior != -1 else corte]

    # tira o bloco de sugestão de título
    miolo = re.sub(r'<div class="nota">\s*<p>\s*<strong>Título sugerido.*?</div>',
                   "", miolo, flags=re.S)
    return miolo.strip()


def main():
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        print("Chromium não encontrado — não dá pra gerar o PDF.")
        sys.exit(1)

    partes = []
    for nome in CAPITULOS:
        arquivo = AQUI / nome
        if not arquivo.exists():
            print(f"Faltando {nome}: rode antes 'python3 gerar_html.py'")
            sys.exit(1)
        miolo = corpo(arquivo)
        if nome.startswith("00_"):
            # cada <hr> das folhas pré-textuais vira uma página
            for pagina in re.split(r"<hr>", miolo):
                partes.append(f'<div class="pagina capa">{pagina}</div>')
        else:
            # a lista de referências começa em página nova
            for pagina in re.split(r"<hr>", miolo):
                partes.append(f'<div class="pagina">{pagina}</div>')

    documento = ('<!doctype html>\n<html lang="pt-BR"><head><meta charset="utf-8">'
                 "<title>TCC — texto parcial</title>\n"
                 f"<style>{CSS_PDF}</style></head><body>\n"
                 + "\n".join(partes) + "\n</body></html>\n")

    with tempfile.TemporaryDirectory() as tmp:
        fonte = Path(tmp) / "tcc.html"
        fonte.write_text(documento)
        subprocess.run(
            [chromium, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-pdf-header-footer", f"--print-to-pdf={SAIDA}", fonte.as_uri()],
            check=True, capture_output=True, timeout=180)

    print(f"PDF gerado: {SAIDA}")
    if DESKTOP.is_dir():
        destino = DESKTOP / SAIDA.name
        shutil.copy(SAIDA, destino)
        print(f"Área de Trabalho: {destino.name}")


if __name__ == "__main__":
    main()
