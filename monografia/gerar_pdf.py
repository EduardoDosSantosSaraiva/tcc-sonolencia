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
    ../venv/bin/python gerar_pdf.py                  # capa + introdução
    ../venv/bin/python gerar_pdf.py --texto          # capa + todos os capítulos escritos
    ../venv/bin/python gerar_pdf.py --tudo           # folhas pré-textuais + todos os capítulos
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
 .folha-capa{position:relative;height:700pt;page-break-after:always}
 .folha-capa div{position:absolute;left:0;right:0;text-align:center;text-indent:0;margin:0}
 .c-inst{top:0}
 .c-autores{top:71pt}
 .c-titulo{top:303pt;font-weight:bold}
 .c-cidade{top:648pt}
 .c-ano{top:672pt}
 .pagina{page-break-after:always}
 .pagina:last-child{page-break-after:auto}
 .capitulo+.capitulo{margin-top:24pt}
 .refs{page-break-before:always}
 p.legenda{text-indent:0;text-align:center;margin:12pt 0 6pt;page-break-after:avoid}
 p.eq{text-indent:0;text-align:center;position:relative;margin:6pt 0 12pt}
 p.eq .num{position:absolute;right:0}
 table{border-collapse:collapse;width:100%;font-size:10pt;line-height:1.1;margin:0;page-break-inside:avoid}
 table+p.legenda{page-break-before:avoid}
 th,td{border:1px solid #000;padding:3pt 4pt;text-align:left;vertical-align:top}
"""

TITULO_REFS = "<h1>REFERÊNCIAS BIBLIOGRÁFICAS</h1>"


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


def separar_referencias(miolo):
    """Cada capítulo lista no fim só as referências que ele cita primeiro,
    já com a numeração global. Devolve (texto sem a lista, {número: <p>})."""
    corte = miolo.find(TITULO_REFS)
    if corte == -1:
        return miolo, {}
    refs = {int(n): p for p, n in
            re.findall(r'(<p class="ref">(\d+)\..*?</p>)', miolo[corte:], re.S)}
    return miolo[:corte].strip(), refs


def conferir_citacoes(texto, refs):
    """Avisa de citação sem referência, referência nunca citada e número novo
    fora da ordem de primeira citação (o sistema numérico da ABNT exige)."""
    citados = [int(n) for n in re.findall(r"<sup>(\d+)</sup>", texto)]
    vistos, maior = set(), 0
    for n in citados:
        if n not in vistos and n != maior + 1:
            print(f"  Aviso: a referência {n} aparece pela primeira vez depois da {maior}.")
        vistos.add(n)
        maior = max(maior, n)
    for n in sorted(vistos - refs.keys()):
        print(f"  Aviso: citação {n} sem referência na lista.")
    for n in sorted(refs.keys() - vistos):
        print(f"  Aviso: referência {n} não é citada no texto.")


def capa():
    """Monta a folha de rosto no layout exato do TCC-modelo.

    As posições verticais (instituição a 3 cm do topo, autores logo abaixo,
    título a 303 pt e cidade/ano no pé) foram medidas no PDF do orientador.
    O conteúdo vem do 00_pre_textual.md, pra não haver duas fontes da verdade.
    """
    fonte = AQUI / "00_pre_textual.md"
    bloco = fonte.read_text().split("\n---\n")[0]
    titulos = [l[2:].strip() for l in bloco.split("\n") if l.startswith("# ")]
    autores = [l[3:].strip() for l in bloco.split("\n") if l.startswith("## ")]
    rodape = [l[4:].strip() for l in bloco.split("\n") if l.startswith("### ")]

    linhas_autores = "<br>".join(autores)
    return (f'<div class="folha-capa">'
            f'<div class="c-inst">{titulos[0]}</div>'
            f'<div class="c-autores">{linhas_autores}</div>'
            f'<div class="c-titulo">{titulos[1]}</div>'
            f'<div class="c-cidade">{rodape[0]}</div>'
            f'<div class="c-ano">{rodape[1]}</div>'
            f'</div>')


def numerar(pdf, primeira=1, pular=0):
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
        if i < pular:          # capa é contada mas não numerada
            escritor.add_page(pagina)
            continue
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.setFont("Times-Roman", 12)
        c.drawRightString(A4[0] - MARGEM_DIREITA_PT, NUMERO_BASELINE_PT, str(primeira + i - pular))
        c.save()
        buffer.seek(0)
        pagina.merge_page(PdfReader(buffer).pages[0])
        escritor.add_page(pagina)
    with open(pdf, "wb") as f:
        escritor.write(f)


def main():
    tudo = "--tudo" in sys.argv
    texto = tudo or "--texto" in sys.argv
    # capítulos escritos até agora: 01_introducao, 02_fundamentacao, ...
    capitulos = (sorted(p.with_suffix(".html").name for p in AQUI.glob("0[1-9]_*.md"))
                 if texto else ["01_introducao.html"])
    for nome in capitulos + (["00_pre_textual.html"] if tudo else []):
        if not (AQUI / nome).exists():
            print(f"Faltando {nome}: rode antes 'python3 gerar_html.py'")
            sys.exit(1)
    if tudo:
        saida = AQUI / "TCC - texto parcial.pdf"
    elif texto:
        saida = AQUI / f"TCC - Capitulos 1 a {int(capitulos[-1][:2])}.pdf"
    else:
        saida = AQUI / "TCC - Introducao.pdf"

    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    if not chromium:
        print("Chromium não encontrado — não dá pra gerar o PDF.")
        sys.exit(1)

    partes = []
    if tudo:
        # nas folhas pré-textuais cada bloco separado por --- é uma página
        for pagina in re.split(r"<hr>", corpo(AQUI / "00_pre_textual.html")):
            if pagina.strip():
                partes.append(f'<div class="pagina capa">{pagina}</div>')
    else:
        partes.append(capa())

    if not texto:
        # só a introdução, do jeito que foi entregue ao orientador
        partes.append(f'<div class="pagina">{corpo(AQUI / "01_introducao.html")}</div>')
    else:
        # texto corrido, capítulo após capítulo, e uma lista única de referências
        refs, textos = {}, []
        for nome in capitulos:
            miolo, novas = separar_referencias(corpo(AQUI / nome))
            for n in sorted(novas.keys() & refs.keys()):
                if novas[n] != refs[n]:
                    print(f"  Aviso: a referência {n} aparece diferente em {nome}.")
            refs.update(novas)
            textos.append(f'<div class="capitulo">{miolo}</div>')
        conferir_citacoes("".join(textos), refs)
        partes += textos
        partes.append(f'<div class="refs">{TITULO_REFS}'
                      + "".join(refs[n] for n in sorted(refs)) + "</div>")

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

    numerar(saida, pular=1 if not tudo else 4)
    print(f"PDF gerado: {saida}")
    if DESKTOP.is_dir():
        shutil.copy(saida, DESKTOP / saida.name)
        print(f"Área de Trabalho: {saida.name}")


if __name__ == "__main__":
    main()
