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

import html as html_mod
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
 .capa.resumo p{text-indent:1.22cm;text-align:justify}   /* como no modelo: corpo justificado, recuo 1,22 cm */
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
 .quadro{page-break-inside:avoid}
 .figura{text-align:center;margin:0}
 .figura svg,.figura img{width:100%;height:auto}
 th,td{border:1px solid #000;padding:3pt 4pt;text-align:left;vertical-align:top}
 /* títulos sem indicativo numérico vão centralizados (ABNT NBR 14724) */
 .refs h1,.lista h1{text-align:center}
 .lista p{text-indent:0}
 /* pontilhado: linha atrás do texto, que tem fundo branco; assim os pontos
    começam onde o texto termina, mesmo quando a legenda quebra em 2 linhas */
 .toc{position:relative;z-index:0;margin:0 0 5pt;padding-right:2.4em;text-align:left}
 .toc::after{content:"";position:absolute;z-index:-1;left:0;right:2.2em;bottom:3.5pt;
             border-bottom:1.2px dotted #000}
 .toc .t{background:#fff;padding-right:3pt}
 .toc .d{display:none}
 .toc .p{position:absolute;right:0;bottom:0}
 .toc.n1{font-weight:bold}
 .toc.n2{padding-left:1.25cm}
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


def _limpo(fragmento):
    """Texto puro de um trecho de HTML, com espaços normalizados."""
    texto = html_mod.unescape(re.sub(r"<[^>]+>", "", fragmento))
    return re.sub(r"\s+", " ", texto).strip()


def itens_do_texto(capitulos_html):
    """Capítulos (h1), seções (h2) e legendas de figuras e quadros, na ordem
    em que aparecem. Cada item: (tipo, texto)."""
    itens = []
    padrao = (r'<h([12])[^>]*>(.*?)</h\1>'
              r'|<p class="legenda">((?:Figura|Quadro) \d+:.*?)</p>')
    for m in re.finditer(padrao, capitulos_html, re.S):
        if m.group(1):
            itens.append(("n" + m.group(1), _limpo(m.group(2))))
        else:
            texto = _limpo(m.group(3))
            itens.append(("figura" if texto.startswith("Figura") else "quadro", texto))
    itens.append(("n1", _limpo(TITULO_REFS)))
    return itens


def paginas_listas_e_sumario(itens, paginas):
    """Lista de figuras, lista de quadros e sumário (nessa ordem, como pede a
    ABNT NBR 14724), cada um em página própria. `paginas` traz o número de
    página de cada item; na primeira passagem ainda não se sabe, vai 000."""
    def linha(classe, texto, pagina):
        texto = re.sub(r"(\S+-\S+)", r'<span style="white-space:nowrap">\1</span>',
                       html_mod.escape(texto))          # não quebra "UTA-RLDD" no hífen
        return (f'<div class="toc {classe}"><span class="t">{texto}</span>'
                f'<span class="d"></span><span class="p">{pagina}</span></div>')

    blocos = []
    for tipo, titulo in (("figura", "LISTA DE FIGURAS"), ("quadro", "LISTA DE QUADROS")):
        linhas = [linha("", t, paginas.get(i, "000")) for i, (k, t) in enumerate(itens) if k == tipo]
        if linhas:
            blocos.append(f'<div class="pagina lista"><h1>{titulo}</h1>{"".join(linhas)}</div>')
    linhas = [linha(k, t, paginas.get(i, "000")) for i, (k, t) in enumerate(itens) if k in ("n1", "n2")]
    blocos.append(f'<div class="pagina lista"><h1>SUMÁRIO</h1>{"".join(linhas)}</div>')
    return blocos


def localizar(pdf, itens):
    """Acha no PDF a página de cada item, procurando a partir da primeira
    página que começa com o título do primeiro capítulo (as listas e o
    sumário vêm antes e repetem os mesmos textos). Devolve (índice da
    página da Introdução, {item: número impresso})."""
    from pypdf import PdfReader
    textos = [re.sub(r"\s+", " ", p.extract_text() or "") for p in PdfReader(pdf).pages]
    primeiro = next(t for k, t in itens if k == "n1")
    inicio = next((i for i, t in enumerate(textos) if t.strip().startswith(primeiro)), None)
    if inicio is None:
        sys.exit(f"Não achei no PDF a página que começa com '{primeiro}'.")
    paginas, atual = {}, inicio
    for n, (_k, texto) in enumerate(itens):
        achou = next((i for i in range(atual, len(textos)) if texto in textos[i]), None)
        if achou is None:
            print(f"  Aviso: não achei no PDF '{texto[:60]}'; usei a página do item anterior.")
            achou = atual
        paginas[n] = achou - inicio + 1
        atual = achou
    return inicio, paginas


def imprimir(documento, saida, chromium):
    with tempfile.TemporaryDirectory() as tmp:
        fonte = Path(tmp) / "tcc.html"
        fonte.write_text(documento)
        subprocess.run(
            [chromium, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-pdf-header-footer", f"--print-to-pdf={saida}", fonte.as_uri()],
            check=True, capture_output=True, timeout=180)


def montar(partes):
    return ('<!doctype html>\n<html lang="pt-BR"><head><meta charset="utf-8">'
            "<title>TCC</title>\n"
            f"<style>{CSS}</style></head><body>\n"
            + "\n".join(partes) + "\n</body></html>\n")


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
                resumo = " resumo" if re.search(r"<h1[^>]*>\s*(Resumo|Abstract)", pagina) else ""
                partes.append(f'<div class="pagina capa{resumo}">{pagina}</div>')
    else:
        partes.append(capa())
    n_pre = len(partes)      # folhas antes do texto; listas e sumário entram depois delas

    if not texto:
        # só a introdução, do jeito que foi entregue ao orientador
        partes.append(f'<div class="pagina">{corpo(AQUI / "01_introducao.html")}</div>')
    else:
        # texto corrido, capítulo após capítulo, e uma lista única de referências
        refs, textos = {}, []
        for nome in capitulos:
            miolo, novas = separar_referencias(corpo(AQUI / nome))
            # título, quadro (ou figura) e fonte sempre na mesma página
            miolo = re.sub(r'(<p class="legenda">(?:Quadro|Figura) .*?(?:</table>|</svg></div>)\s*<p class="legenda">Fonte[^<]*</p>)',
                           r'<div class="quadro">\1</div>', miolo, flags=re.S)
            for n in sorted(novas.keys() & refs.keys()):
                if novas[n] != refs[n]:
                    print(f"  Aviso: a referência {n} aparece diferente em {nome}.")
            refs.update(novas)
            textos.append(f'<div class="capitulo">{miolo}</div>')
        conferir_citacoes("".join(textos), refs)
        partes += textos
        partes.append(f'<div class="refs">{TITULO_REFS}'
                      + "".join(refs[n] for n in sorted(refs)) + "</div>")

    if tudo:
        # Listas e sumário precisam das páginas, que só se sabem depois de
        # imprimir: a 1ª passagem sai com 000 no lugar dos números, a 2ª com
        # os números achados no PDF. Se a 2ª mudar a paginação, faz a 3ª.
        itens = itens_do_texto("".join(textos))
        paginas, inicio = {}, None
        for _ in range(3):
            imprimir(montar(partes[:n_pre] + paginas_listas_e_sumario(itens, paginas) + partes[n_pre:]),
                     saida, chromium)
            novo_inicio, novas = localizar(saida, itens)
            if novas == paginas and novo_inicio == inicio:
                break
            inicio, paginas = novo_inicio, novas
        else:
            print("  Aviso: a paginação não estabilizou; confira os números do sumário.")
        pular = inicio          # a Introdução é a página 1, como no TCC-modelo
    else:
        imprimir(montar(partes), saida, chromium)
        pular = 1

    numerar(saida, pular=pular)
    print(f"PDF gerado: {saida}")
    if DESKTOP.is_dir():
        shutil.copy(saida, DESKTOP / saida.name)
        print(f"Área de Trabalho: {saida.name}")


if __name__ == "__main__":
    main()
