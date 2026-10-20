#!/usr/bin/env python3
"""
Converte os arquivos .md da monografia em .html formatado em ABNT
(Times New Roman 12, justificado, entrelinha 1,15, recuo de parágrafo e
citações em sobrescrito de verdade) e copia o resultado para a Área de
Trabalho, pronto pra colar no Google Docs preservando a formatação.

Uso:
    python3 gerar_html.py            # converte todos os .md da pasta
    python3 gerar_html.py 01_introducao.md
"""

import html
import re
import shutil
import sys
import unicodedata
from pathlib import Path

AQUI = Path(__file__).parent
DESKTOP = Path.home() / "Desktop"

# nome amigável usado na cópia da Área de Trabalho
# só a introdução vai pra Área de Trabalho: é o que o orientador pediu
APELIDOS = {
    "01_introducao": "TCC - Introducao",
}

SUPERSCRITOS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}

CSS = """
 body{font-family:"Times New Roman",Times,serif;font-size:12pt;line-height:1.15;
      max-width:16cm;margin:2cm auto;padding:0 1cm;color:#000;text-align:justify}
 h1{font-size:12pt;font-weight:bold;text-align:left;text-transform:uppercase;margin:24pt 0 18pt}
 h2{font-size:12pt;font-weight:bold;text-align:left;margin:18pt 0 10pt}
 h3{font-size:12pt;font-weight:normal;text-align:center;margin:1em 0}
 p{text-indent:1.22cm;margin:0 0 10pt}
 p.ref{text-indent:0;margin:0 0 10pt}
 sup{font-size:.7em;vertical-align:super;line-height:0}
 .nota{background:#f4f4f4;border-left:3px solid #999;padding:.6em 1em;margin:0 0 1.5em}
 .nota p{text-indent:0;font-size:11pt}
 ul{margin:0 0 10pt 0}li{text-align:justify;margin-bottom:.3em}
 code{font-family:"Courier New",monospace;font-size:11pt}
 hr{border:0;border-top:1px solid #bbb;margin:2.5em 0}
 a{color:#000}
 body.capa{text-align:center}
 body.capa h1,body.capa h2{text-align:center;margin:1.6em 0}
 body.capa p{text-indent:0;text-align:justify}
 p.legenda{text-indent:0;text-align:center;margin:12pt 0 6pt}
 p.eq{text-indent:0;text-align:center;position:relative;margin:6pt 0 12pt}
 p.eq .num{position:absolute;right:0}
 table{border-collapse:collapse;width:100%;font-size:10pt;line-height:1.1;margin:0;page-break-inside:avoid}
 th,td{border:1px solid #000;padding:3pt 4pt;text-align:left;vertical-align:top}
 .figura{text-align:center;margin:0}
 .figura svg,.figura img{width:100%;height:auto}
 @media print{body{margin:0;max-width:none}}
"""


def inline(texto):
    t = html.escape(texto)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"`([^`]+?)`", r"<code>\1</code>", t)
    t = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', t)
    t = t.replace("\\_", "_")
    # índice: p~2~ vira p com 2 subscrito (variáveis das equações)
    t = re.sub(r"~([^~\s]{1,12})~", r"<sub>\1</sub>", t)
    # sequências de dígitos sobrescritos viram <sup> de verdade
    t = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+",
               lambda m: "<sup>" + "".join(SUPERSCRITOS[c] for c in m.group(0)) + "</sup>", t)
    return t


def converter(caminho_md):
    origem = Path(caminho_md)
    linhas, em_lista, em_nota, em_tabela = [], False, False, False

    def fecha_lista():
        nonlocal em_lista
        if em_lista:
            linhas.append("</ul>")
            em_lista = False

    def fecha_nota():
        nonlocal em_nota
        if em_nota:
            linhas.append("</div>")
            em_nota = False

    def fecha_tabela():
        nonlocal em_tabela
        if em_tabela:
            linhas.append("</table>")
            em_tabela = False

    for bruta in origem.read_text().split("\n"):
        linha = bruta.rstrip()
        if not linha.strip():
            fecha_lista()
            fecha_nota()
            fecha_tabela()
            continue
        # quadro: | a | b |, a primeira linha é o cabeçalho e |---| é ignorada
        if linha.startswith("|"):
            celulas = [c.strip() for c in linha.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in celulas):
                continue
            tag = "td" if em_tabela else "th"
            if not em_tabela:
                linhas.append("<table>")
                em_tabela = True
            linhas.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in celulas) + "</tr>")
            continue
        fecha_tabela()
        # legenda centralizada de figura/quadro e a linha de fonte: "::: texto"
        if linha.startswith("::: "):
            fecha_lista()
            linhas.append('<p class="legenda">' + inline(linha[4:]) + "</p>")
            continue
        # figura: "![texto](figuras/arquivo.svg)". O SVG vai embutido no HTML,
        # pra o PDF (montado numa pasta temporária) não perder o caminho
        figura = re.fullmatch(r"!\[(.*?)\]\((.+?)\)", linha.strip())
        if figura:
            fecha_lista()
            arquivo = (origem.parent / figura.group(2)).resolve()
            if arquivo.suffix.lower() == ".svg":
                conteudo = re.sub(r"<\?xml.*?\?>", "", arquivo.read_text()).strip()
            else:
                conteudo = f'<img src="{arquivo.as_uri()}" alt="{html.escape(figura.group(1))}">'
            linhas.append(f'<div class="figura">{conteudo}</div>')
            continue
        # equação centralizada com o número à direita: "$$ fórmula | (1)"
        if linha.startswith("$$ "):
            fecha_lista()
            formula, _, numero = linha[3:].rpartition(" | ")
            formula = formula or numero
            numero = numero if formula is not numero else ""
            linhas.append(f'<p class="eq">{inline(formula)}<span class="num">{html.escape(numero)}</span></p>')
            continue
        if linha.strip() == ">" and em_nota:
            continue  # linha em branco dentro de uma citação: mantém o bloco aberto
        if linha.startswith("> "):
            if not em_nota:
                linhas.append('<div class="nota">')
                em_nota = True
            linhas.append("<p>" + inline(linha[2:]) + "</p>")
            continue
        fecha_nota()
        for nivel in (3, 2, 1):
            marca = "#" * nivel + " "
            if linha.startswith(marca):
                fecha_lista()
                linhas.append(f"<h{nivel}>" + inline(linha[len(marca):]) + f"</h{nivel}>")
                break
        else:
            if linha.strip() == "---":
                fecha_lista()
                linhas.append("<hr>")
            elif linha.startswith("- "):
                if not em_lista:
                    linhas.append("<ul>")
                    em_lista = True
                linhas.append("<li>" + inline(linha[2:]) + "</li>")
            else:
                fecha_lista()
                classe = ' class="ref"' if re.match(r"^\d+\.\s", linha) else ""
                linhas.append(f"<p{classe}>" + inline(linha) + "</p>")
    fecha_lista()
    fecha_nota()
    fecha_tabela()

    corpo_classe = ' class="capa"' if origem.stem.startswith("00_") else ""
    titulo = APELIDOS.get(origem.stem, origem.stem)
    doc = (f'<!doctype html>\n<html lang="pt-BR"><head><meta charset="utf-8">\n'
           f"<title>{html.escape(titulo)}</title>\n<style>{CSS}</style></head>"
           f"<body{corpo_classe}>\n" + "\n".join(linhas) + "\n</body></html>\n")

    destino = origem.with_suffix(".html")
    destino.write_text(doc)

    copias = []
    if DESKTOP.is_dir():
        apelido = APELIDOS.get(origem.stem)
        if apelido:
            for sufixo in (".md", ".html"):
                alvo = DESKTOP / (apelido + sufixo)
                shutil.copy(origem.with_suffix(sufixo), alvo)
                copias.append(alvo)
    return destino, copias


def main():
    alvos = sys.argv[1:] or sorted(str(p) for p in AQUI.glob("*.md"))
    for alvo in alvos:
        destino, copias = converter(alvo)
        print(f"{Path(alvo).name} -> {destino.name}")
        for c in copias:
            print(f"    Área de Trabalho: {c.name}")


if __name__ == "__main__":
    main()
