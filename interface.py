"""Telas do programa, desenhadas num quadro do tamanho da tela de destino.

Dois layouts, escolhidos pelo tamanho da tela (Interface.para_tela):
- amplo, 1280x720 em janela (monitor HDMI: testes e apresentação): câmera
  4:3 à esquerda e painel lateral com o estado, medidores, contadores, linha
  do tempo e situação do sistema;
- compacto, na tela inteira (telinha de 3,5", 480x320, no carro): faixa de
  estado no alto, câmera, contadores ao lado e rodapé; o alarme toma a tela.

Telas: espera do motorista, contagem antes da calibração, calibração,
mensagem (mostrada enquanto a voz fala, porque a fala trava o laço),
monitoramento, autoteste (cenário, resultado e resumo).

Desempenho: o laço de detecção já usa ~25 dos 33 ms de cada quadro, então
nada aqui pode ser caro. O texto (Nunito Sans, com acentos) é renderizado
pelo Pillow uma vez e guardado como máscara; desenhar é só misturar a
máscara na imagem. O resto é OpenCV. A câmera é reduzida antes de converter
a cor, que assim custa menos.

Nada aqui decide estado: só mostra o que core.DrowsinessState decidiu.
"""
import math
import re
import time
from collections import OrderedDict

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import core

FONTE = "/usr/share/fonts/truetype/nunito-sans/NunitoSans-VariableFont_YTLC,opsz,wdth,wght.ttf"
FONTE_RESERVA = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
ESPELHAR = False  # imagem como num espelho (mexer para a esquerda move a imagem para
                  # a esquerda) ajuda a se posicionar, mas custa 1,25 ms por quadro
                  # no Pi 5 (960x720); a tela antiga e os prints de alarme também
                  # não espelham


def _hex(h):
    """'#RRGGBB' -> tupla BGR do OpenCV."""
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


COR = {
    "fundo": _hex("#0D1116"),
    "painel": _hex("#151B22"),
    "cartao": _hex("#1D252E"),
    "trilho": _hex("#2B353F"),
    "texto": _hex("#EEF2F6"),
    "suave": _hex("#9AA6B2"),
    "apagado": _hex("#5F6B78"),
    "ok": _hex("#22C55E"),
    "aviso": _hex("#F5A524"),
    "laranja": _hex("#F97316"),
    "perigo": _hex("#EF4444"),
    "info": _hex("#3B82F6"),
    "neutro": _hex("#64748B"),
}
# gravidade (linha do tempo): -1 sem dado, 0 sem rosto, 1 atento, 2 atenção/fadiga,
# 3 distração, 4 sono
COR_GRAVIDADE = ["trilho", "neutro", "ok", "aviso", "laranja", "perigo"]


def _cor(c):
    return COR[c] if isinstance(c, str) else c


def _num(x, casas=1):
    """Número com vírgula decimal."""
    return f"{x:.{casas}f}".replace(".", ",")


def _mmss(seg):
    seg = int(max(0, seg))
    h, m, s = seg // 3600, (seg % 3600) // 60, seg % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _escurecer(cor, f):
    return tuple(int(c * f) for c in _cor(cor))


def classificar(st):
    """(título, detalhe, cor, gravidade) do estado devolvido por
    DrowsinessState.update, em português com acento."""
    if st is None:
        return ("INICIANDO", "", "neutro", -1)
    nivel = st.get("level")
    status = st.get("status", "")
    if st.get("distraido"):
        m = re.search(r"(\d+)s", status)
        return ("OLHE PARA A ESTRADA",
                f"Rosto fora da câmera há {m.group(1)} s" if m else "Rosto fora da câmera", "laranja", 3)
    if nivel == "fechado":
        return ("SONO DETECTADO", f"Olhos fechados há {_num(st.get('closed_dur') or 0)} s", "perigo", 4)
    if nivel == "fechando" and (st.get("closed_dur") or 0) < core.LONG_BLINK_SECONDS:
        return ("ATENTO", "Piscando", "ok", 1)   # piscada normal não muda o cartão
    if nivel == "fechando":
        d = _num(st.get("closed_dur") or 0)
        return ("OLHOS FECHANDO",
                f"Sorrindo de olhos fechados, {d} s" if "sorrindo" in status else f"Fechados há {d} s", "aviso", 2)
    if nivel == "atencao":
        if st.get("atencao_by_yawn"):
            if "frequentes" in status:
                sub = f"Bocejos frequentes: {st.get('yawn_count_recent', 0)} em {core.YAWN_WINDOW_SECONDS // 60} min"
            elif "olhos fechados" in status:
                sub = "Bocejando de olhos fechados"
            else:
                sub = "Bocejo em andamento"
            return ("FADIGA", sub, "aviso", 2)
        if st.get("atencao_by_head"):
            sub = "Cabeça caindo com frequência" if "frequencia" in status else "Cabeça caindo"
        elif st.get("atencao_by_perclos"):
            sub = f"Olhos fechados {st.get('perclos', 0) * 100:.0f}% do último minuto"
        elif st.get("atencao_by_blinks"):
            sub = (f"Piscadas longas: {st.get('blink_count_recent', 0)} em "
                   f"{core.LONG_BLINK_WINDOW_SECONDS // 60} min")
        else:
            sub = "Olhos semicerrados"
        return ("ATENÇÃO", sub, "aviso", 2)
    if nivel == "aberto":
        return ("ATENTO", "Olhos abertos", "ok", 1)
    if nivel == "sem_rosto":
        return ("SEM ROSTO", "Sente-se em frente à câmera", "neutro", 0)
    if nivel == "geometria_invalida":
        return ("OLHOS NÃO VISÍVEIS", "Ajuste a posição da câmera", "neutro", 0)
    return ("RASTREANDO", "", "neutro", 0)


class _Textos:
    """Máscaras de texto em cache: o Pillow renderiza cada (texto, tamanho,
    peso) uma vez; depois é só misturar a máscara na imagem."""

    def __init__(self, limite=800):
        self._fontes = {}
        self._cache = OrderedDict()
        self._larguras = {}
        self.limite = limite

    def fonte(self, tam, peso):
        f = self._fontes.get((tam, peso))
        if f is None:
            try:
                f = ImageFont.truetype(FONTE, tam)
                f.set_variation_by_name(peso)
            except Exception:
                try:
                    f = ImageFont.truetype(FONTE_RESERVA, tam)
                except Exception:
                    f = ImageFont.load_default()
            self._fontes[(tam, peso)] = f
        return f

    def largura(self, s, tam, peso):
        chave = (s, tam, peso)
        w = self._larguras.get(chave)
        if w is None:
            if len(self._larguras) > 4000:
                self._larguras.clear()
            w = self._larguras[chave] = int(math.ceil(self.fonte(tam, peso).getlength(s)))
        return w

    def mascara(self, s, tam, peso):
        chave = (s, tam, peso)
        m = self._cache.get(chave)
        if m is not None:
            self._cache.move_to_end(chave)
            return m
        f = self.fonte(tam, peso)
        asc, desc = f.getmetrics()
        img = Image.new("L", (max(1, self.largura(s, tam, peso) + 2), asc + desc), 0)
        ImageDraw.Draw(img).text((1, 0), s, font=f, fill=255)
        a = np.asarray(img, dtype=np.float32) / 255.0
        m = (a, 1.0 - a)
        self._cache[chave] = m
        if len(self._cache) > self.limite:
            self._cache.popitem(last=False)
        return m

    def quebrar(self, s, tam, peso, max_w):
        linhas, atual = [], ""
        for palavra in s.split():
            teste = f"{atual} {palavra}".strip()
            if atual and self.largura(teste, tam, peso) > max_w:
                linhas.append(atual)
                atual = palavra
            else:
                atual = teste
        if atual:
            linhas.append(atual)
        return linhas


_solidos = {}


def _solido(cor, h, w):
    """Retalho h x w de cor sólida (recorte de uma faixa guardada por cor)."""
    c = _cor(cor)
    s = _solidos.get(c)
    if s is None or s.shape[0] < h or s.shape[1] < w:
        s = np.empty((max(h, 256), max(w, 1280), 3), np.uint8)
        s[:] = np.array(c, np.uint8)
        _solidos[c] = s
    return s[:h, :w]


def _misturar(img, x, y, m, cor, alpha=1.0):
    """Pinta a cor na imagem onde a máscara manda (m = (alfa, 1 - alfa), em
    float32), recortando nas bordas. cv2.blendLinear: ~8x mais rápido que
    fazer a conta em numpy."""
    a, inv = m
    h, w = a.shape
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    a = a[y0 - y:y1 - y, x0 - x:x1 - x]
    if alpha != 1.0:
        a = a * alpha
        inv = 1.0 - a
    else:
        inv = inv[y0 - y:y1 - y, x0 - x:x1 - x]
    reg = img[y0:y1, x0:x1]
    cv2.blendLinear(reg, _solido(cor, y1 - y0, x1 - x0), inv, a, dst=reg)


def _recorte(img, x, y, w, h):
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(W, int(x + w)), min(H, int(y + h))
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def tingir(img, x, y, w, h, cor, alpha):
    """Retângulo de cor uniforme com transparência, direto nos pixels."""
    rc = _recorte(img, x, y, w, h)
    if rc:
        reg = img[rc[1]:rc[3], rc[0]:rc[2]]
        cv2.convertScaleAbs(reg, dst=reg, alpha=1.0 - alpha)
        cv2.add(reg, tuple(float(c) * alpha for c in _cor(cor)) + (0.0,), dst=reg)


def _caixa_direta(img, x, y, w, h, c, r):
    r = max(0, min(r, w // 2, h // 2))
    if r == 0:
        cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), c, -1)
        return
    cv2.rectangle(img, (x + r, y), (x + w - 1 - r, y + h - 1), c, -1)
    cv2.rectangle(img, (x, y + r), (x + w - 1, y + h - 1 - r), c, -1)
    for cx, cy in ((x + r, y + r), (x + w - 1 - r, y + r), (x + r, y + h - 1 - r), (x + w - 1 - r, y + h - 1 - r)):
        cv2.circle(img, (cx, cy), r, c, -1, cv2.LINE_AA)


def caixa(img, x, y, w, h, cor, r=10, alpha=1.0):
    """Retângulo de cantos arredondados, opaco ou transparente."""
    x, y, w, h, r = int(x), int(y), int(w), int(h), int(r)
    if w <= 0 or h <= 0:
        return
    c = _cor(cor)
    if alpha >= 0.999:
        _caixa_direta(img, x, y, w, h, c, r)
    elif r == 0:
        tingir(img, x, y, w, h, c, alpha)
    else:
        rc = _recorte(img, x, y, w, h)
        if rc:
            sub = img[rc[1]:rc[3], rc[0]:rc[2]]
            ov = sub.copy()
            _caixa_direta(ov, x - rc[0], y - rc[1], w, h, c, r)
            cv2.addWeighted(ov, alpha, sub, 1.0 - alpha, 0, dst=sub)


def pilula(img, x, y, w, h, frac, cor, trilho="trilho"):
    """Barra arredondada preenchida até frac (0..1)."""
    r = h // 2
    cy = int(y + r)
    cv2.line(img, (int(x + r), cy), (int(x + w - r), cy), _cor(trilho), int(h), cv2.LINE_AA)
    frac = min(1.0, max(0.0, frac))
    if frac > 0:
        cv2.line(img, (int(x + r), cy), (int(x + r + (w - 2 * r) * frac), cy), _cor(cor), int(h), cv2.LINE_AA)


class LinhaDoTempo:
    """Gravidade do estado nos últimos minutos, em colunas de tempo fixo (cada
    coluna guarda a pior gravidade vista nela)."""

    def __init__(self, janela_s=600, colunas=240):
        self.janela_s = janela_s
        self.n = colunas
        self.passo = janela_s / colunas
        self.sev = np.full(colunas, -1, np.int8)
        self.ultimo = None

    def registrar(self, t, gravidade):
        b = int(t // self.passo)
        if self.ultimo is None or b < self.ultimo - self.n:   # começo ou vídeo recomeçado
            self.sev[:] = -1
            self.ultimo = b
        if b > self.ultimo:
            for k in range(self.ultimo + 1, min(b, self.ultimo + self.n) + 1):
                self.sev[k % self.n] = -1
            self.ultimo = b
        if b == self.ultimo:
            i = b % self.n
            self.sev[i] = max(self.sev[i], gravidade)

    def serie(self):
        if self.ultimo is None:
            return np.full(self.n, -1, np.int8)
        return self.sev[np.arange(self.ultimo - self.n + 1, self.ultimo + 1) % self.n]


class Interface:
    AMPLO = (1280, 720)
    CAM_W = 960            # câmera 4:3 do layout amplo (960x720)
    PAINEL_PAD = 24

    def __init__(self, largura=1280, altura=720, compacto=False):
        self.W, self.H = int(largura), int(altura)
        self.compacto = compacto
        self.s = min(self.W / 480, self.H / 320) if compacto else 1.0
        self.tecnico = False          # dados técnicos (tecla D ou toque na tela)
        self.txt = _Textos()
        self.ultimo_quadro = None     # última imagem da câmera (RGB), fundo das mensagens
        self.linha = LinhaDoTempo()
        self.inicio = None            # começo do monitoramento (tempo do quadro)
        self._estado = None           # (classificação, t): segura falhas curtas de rastreamento
        self._lut = np.array([_cor(c) for c in COR_GRAVIDADE], np.uint8)
        self._fixos = {}
        self._fps = self._lat = None   # médias móveis (o texto não fica pulando)

    @classmethod
    def para_tela(cls, tela):
        """tela: (nome, x, y, largura, altura) de pick_screen, ou None. Tela
        que comporta 1280x720 com a barra de título: layout amplo em janela;
        menor: compacto ocupando a tela inteira."""
        if tela is not None and (tela[3] < cls.AMPLO[0] or tela[4] < cls.AMPLO[1] + 40):
            return cls(tela[3], tela[4], compacto=True)
        return cls(*cls.AMPLO)

    # ------------------------------------------------------------ primitivas
    def _p(self, v):
        return int(round(v * self.s))

    def texto(self, img, s, x, y, tam, cor="texto", peso="Regular", ax="l", ay="t", max_w=None, alpha=1.0):
        """Escreve s com âncora horizontal ax (l, c, r) e vertical ay (t, m, b).
        tam em pontos da tela de referência (escala no compacto). Se não couber
        em max_w, diminui a fonte até 70% e depois corta com reticências.
        Devolve a largura escrita."""
        if not s:
            return 0
        tam = max(8, self._p(tam))
        if max_w is not None:
            minimo = max(8, int(tam * 0.7))
            while tam > minimo and self.txt.largura(s, tam, peso) > max_w:
                tam -= 1
            while len(s) > 1 and self.txt.largura(s, tam, peso) > max_w:
                s = s[:-2].rstrip() + "…"
        m = self.txt.mascara(s, tam, peso)
        h, w = m[0].shape
        x = int(x - (w / 2 if ax == "c" else w if ax == "r" else 0))
        y = int(y - (h / 2 if ay == "m" else h if ay == "b" else 0))
        _misturar(img, x, y, m, cor, alpha)
        return w

    def paragrafo(self, img, s, x, y, tam, max_w, cor="texto", peso="Regular", entre=1.3, ax="l", max_linhas=None):
        """Texto quebrado em linhas; devolve o y logo abaixo do parágrafo."""
        t = max(8, self._p(tam))
        linhas = self.txt.quebrar(s, t, peso, max_w)
        if max_linhas and len(linhas) > max_linhas:
            linhas = linhas[:max_linhas]
            linhas[-1] = linhas[-1].rstrip(".,") + "…"
        passo = int(t * entre)
        for i, linha in enumerate(linhas):
            self.texto(img, linha, x, y + i * passo, tam, cor, peso, ax=ax)
        return y + len(linhas) * passo

    def _camera(self, frame_rgb, dst, escurecer=None):
        """Escreve a câmera em dst (um recorte da tela), recortada para
        preencher sem distorcer, em BGR e espelhada. Devolve a função que
        leva um landmark normalizado ao pixel de dst (None sem imagem).
        Ordem medida no Pi 5: reduzir, converter a cor no lugar e espelhar
        direto no destino (3,1 ms em 960x720, contra 5,5 com cópias)."""
        if frame_rgb is None:
            frame_rgb = self.ultimo_quadro
        if frame_rgb is None:
            dst[:] = np.array(COR["fundo"], np.uint8)
            return None
        h, w = dst.shape[:2]
        fh, fw = frame_rgb.shape[:2]
        esc = max(w / fw, h / fh)
        cw, ch = min(fw, int(round(w / esc))), min(fh, int(round(h / esc)))
        x0, y0 = (fw - cw) // 2, (fh - ch) // 2
        img = cv2.resize(frame_rgb[y0:y0 + ch, x0:x0 + cw], (w, h), interpolation=cv2.INTER_LINEAR)
        if ESPELHAR:
            cv2.cvtColor(img, cv2.COLOR_RGB2BGR, dst=img)
            cv2.flip(img, 1, dst=dst)
        else:
            cv2.cvtColor(img, cv2.COLOR_RGB2BGR, dst=dst)
        if escurecer is not None:
            cv2.convertScaleAbs(dst, dst=dst, alpha=escurecer)

        def mapa(u, v):
            px = (u * fw - x0) * esc
            return (int(w - px) if ESPELHAR else int(px), int((v * fh - y0) * esc))
        return mapa

    # pontos do contorno do rosto (testa, queixo, laterais) para as cantoneiras:
    # mapear os 478 landmarks a cada quadro custava 0,6 ms
    CONTORNO = (10, 152, 234, 454, 127, 356, 172, 397)

    def _rosto(self, img, landmarks, mapa, cor):
        """Cantoneiras em volta do rosto e pontos dos olhos e da boca."""
        if not landmarks or mapa is None or len(landmarks) <= max(self.CONTORNO):
            return
        pts = [mapa(landmarks[i].x, landmarks[i].y) for i in self.CONTORNO]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        folga = int((x1 - x0) * 0.10)
        x0, x1, y0, y1 = x0 - folga, x1 + folga, y0 - folga, y1 + folga
        lado = max(10, int((x1 - x0) * 0.18))
        esp = max(2, self._p(3)) if self.compacto else 3
        c = _cor(cor)
        for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            cv2.line(img, (cx, cy), (cx + dx * lado, cy), c, esp, cv2.LINE_AA)
            cv2.line(img, (cx, cy), (cx, cy + dy * lado), c, esp, cv2.LINE_AA)
        r = max(1, self._p(2)) if self.compacto else 2
        for i in core.LEFT_EYE + core.RIGHT_EYE + core.MOUTH:
            cv2.circle(img, mapa(landmarks[i].x, landmarks[i].y), r, c, -1, cv2.LINE_AA)

    def _guia_rosto(self, img, cor, cx=None):
        """Oval onde o rosto deve ficar, durante a espera e a calibração."""
        h, w = img.shape[:2]
        cx = w // 2 if cx is None else cx
        if self.compacto:
            centro, eixos = int(h * 0.33), (int(h * 0.17), int(h * 0.25))
        else:
            centro, eixos = int(h * 0.46), (int(h * 0.20), int(h * 0.29))
        cv2.ellipse(img, (cx, centro), eixos, 0, 0, 360, _cor(cor), max(2, self._p(3)), cv2.LINE_AA)

    def _classificar(self, st, t):
        """Classificação do estado; falhas curtas de rastreamento (rastreando,
        olho não confiável) mantêm o cartão anterior por 1 s, sem piscar."""
        c = classificar(st)
        nivel = st.get("level") if st else None
        if nivel in ("sem_rosto_tolerancia", "geometria_invalida") and self._estado and t - self._estado[1] < 1.0:
            return self._estado[0]
        self._estado = (c, t)
        return c

    def _pulso(self, hz=1.6):
        return 0.5 + 0.5 * math.sin(2 * math.pi * hz * time.monotonic())

    def _borda_alarme(self, img, cor):
        h, w = img.shape[:2]
        e = self._p(10) if self.compacto else 12
        a = 0.45 + 0.55 * self._pulso()
        for x, y, ww, hh in ((0, 0, w, e), (0, h - e, w, e), (0, e, e, h - 2 * e), (w - e, e, e, h - 2 * e)):
            tingir(img, x, y, ww, hh, cor, a)

    def _fixo(self, chave, desenhar=None):
        """Imagem do tamanho da tela desenhada uma vez só (fundo, painel com os
        textos que não mudam); preencher a tela inteira a cada quadro custava
        8 ms, copiar custa 0,6."""
        img = self._fixos.get(chave)
        if img is None:
            img = np.empty((self.H, self.W, 3), np.uint8)
            img[:] = np.array(COR["fundo"], np.uint8)
            if not self.compacto:
                img[:, self.CAM_W:] = np.array(COR["painel"], np.uint8)
                cv2.line(img, (self.CAM_W, 0), (self.CAM_W, self.H), COR["cartao"], 1)
            if desenhar:
                desenhar(img)
            self._fixos[chave] = img
        return img

    def _quadro(self):
        return self._fixo("fundo").copy()

    def _painel_lateral(self, img):
        x = self.CAM_W
        img[:, x:] = self._fixo("fundo")[:, x:]
        return x + self.PAINEL_PAD, self.W - x - 2 * self.PAINEL_PAD

    def _cabecalho(self, img, x):
        self.texto(img, "Detecção de Sonolência", x, 22, 19, "texto", "Bold")

    def _marca(self, img, cx, cy, r, tipo, cor):
        """Círculo com ✓ (ok), ✗ (falha), número ou ponto (atual); desenhado
        com linhas porque a Nunito Sans não tem esses símbolos."""
        c = _cor(cor)
        cheio = tipo in ("ok", "falha", "atual") or isinstance(tipo, int)
        cv2.circle(img, (cx, cy), r, c, -1 if cheio else 2, cv2.LINE_AA)
        esp = max(2, r // 5)
        f = _cor("fundo")
        if tipo == "ok":
            pts = np.array([(cx - r * 0.45, cy + r * 0.02), (cx - r * 0.12, cy + r * 0.36),
                            (cx + r * 0.48, cy - r * 0.32)], np.int32)
            cv2.polylines(img, [pts], False, f, esp, cv2.LINE_AA)
        elif tipo == "falha":
            d = int(r * 0.38)
            cv2.line(img, (cx - d, cy - d), (cx + d, cy + d), f, esp, cv2.LINE_AA)
            cv2.line(img, (cx - d, cy + d), (cx + d, cy - d), f, esp, cv2.LINE_AA)
        elif tipo == "atual":
            cv2.circle(img, (cx, cy), max(2, r // 3), f, -1, cv2.LINE_AA)
        elif isinstance(tipo, int):
            self.texto(img, str(tipo), cx, cy, r * 1.05 / max(self.s, 1e-6), "fundo", "Black", ax="c", ay="m")

    # ------------------------------------------------------------ etapas iniciais
    ETAPAS = ("Motorista na posição", "Calibração", "Monitoramento")

    def _etapas(self, img, x, y, w, atual, dicas):
        """Lista das etapas até o monitoramento, com a etapa atual destacada."""
        for i, nome in enumerate(self.ETAPAS):
            cy = y + i * 64
            feito, agora = i < atual, i == atual
            cor = "ok" if feito else "info" if agora else "trilho"
            self._marca(img, x + 14, cy + 14, 14, "ok" if feito else (i + 1) if agora else None, cor)
            if not (feito or agora):
                self.texto(img, str(i + 1), x + 14, cy + 14, 15, "apagado", "Black", ax="c", ay="m")
            self.texto(img, nome, x + 42, cy + 3, 17, "texto" if (feito or agora) else "apagado",
                       "Bold" if agora else "SemiBold")
            if i < len(self.ETAPAS) - 1:
                cv2.line(img, (x + 14, cy + 34), (x + 14, cy + 58), _cor("trilho"), 2, cv2.LINE_AA)
        y_dica = y + len(self.ETAPAS) * 64 + 16
        for dica in dicas:
            y_dica = self.paragrafo(img, dica, x, y_dica, 15, w, "suave") + 8

    def _tela_etapa(self, frame_rgb, etapa, titulo, texto, cor, guia=None, progresso=None,
                    numero=None, dicas=(), landmarks=None, lateral=None):
        """Tela das etapas antes do monitoramento: câmera com o guia do rosto e
        um cartão com o que fazer; no amplo, as etapas no painel lateral (ou o
        que lateral(img, x, w) desenhar)."""
        if frame_rgb is not None:
            self.ultimo_quadro = frame_rgb
        if self.compacto:
            img = np.empty((self.H, self.W, 3), np.uint8)
            self._camera(frame_rgb, img, escurecer=0.55)
            if guia:
                self._guia_rosto(img, guia)
            self._cartao_central(img, 0, 0, self.W, self.H, titulo, texto, cor, progresso, numero,
                                 embaixo=guia is not None)
            return img
        img = np.empty((self.H, self.W, 3), np.uint8)
        cam = img[:, :self.CAM_W]
        mapa = self._camera(frame_rgb, cam, escurecer=0.6)
        if guia:
            self._guia_rosto(cam, guia)
        if landmarks:
            self._rosto(cam, landmarks, mapa, guia or "info")
        self._cartao_central(cam, 0, 0, self.CAM_W, self.H, titulo, texto, cor, progresso, numero, embaixo=True)
        x, w = self._painel_lateral(img)
        self._cabecalho(img, x)
        if lateral:
            lateral(img, x, w)
        else:
            self._etapas(img, x, 92, w, etapa, dicas)
        self._rodape_teclas(img, x)
        return img

    def _cartao_central(self, img, x, y, w, h, titulo, texto, cor, progresso=None, numero=None, embaixo=False):
        """Cartão com título, texto, número grande (contagem) e barra de
        progresso. embaixo=True o põe na parte de baixo, sem cobrir o rosto."""
        p = self._p
        cw = min(w - p(32), p(560) if self.compacto else 620)
        tam_tit, tam_txt = (24, 15) if self.compacto else (30, 18)
        linhas = self.txt.quebrar(texto, max(8, p(tam_txt)), "Regular", cw - p(48)) if texto else []
        selo = numero is not None and self.compacto
        alt = p(28) + int(p(tam_tit) * 1.25) + len(linhas) * int(p(tam_txt) * 1.35) + p(24)
        if progresso is not None:
            alt += p(26)
        if numero is not None and not selo:
            alt += p(80)
        if selo:
            alt += p(18)
        cx0 = x + (w - cw) // 2
        cy0 = y + (h - alt - p(16 if self.compacto else 28) if embaixo else (h - alt) // 2)
        caixa(img, cx0, cy0, cw, alt, "painel", r=p(16), alpha=0.92)
        caixa(img, cx0 + p(10), cy0 + p(16), p(5), alt - p(32), cor, r=p(2))
        yy = cy0 + p(22)
        if selo:
            cv2.circle(img, (cx0 + cw // 2, cy0), p(26), _cor(cor), -1, cv2.LINE_AA)
            self.texto(img, str(numero), cx0 + cw // 2, cy0, 32, "texto", "Black", ax="c", ay="m")
            yy += p(18)
        elif numero is not None:
            self.texto(img, str(numero), cx0 + cw // 2, yy, 64, cor, "Black", ax="c")
            yy += p(80)
        self.texto(img, titulo, cx0 + cw // 2, yy, tam_tit, "texto", "ExtraBold", ax="c", max_w=cw - p(40))
        yy += int(p(tam_tit) * 1.35)
        for i, linha in enumerate(linhas):
            self.texto(img, linha, cx0 + cw // 2, yy + i * int(p(tam_txt) * 1.35), tam_txt, "suave", ax="c")
        yy += len(linhas) * int(p(tam_txt) * 1.35) + p(8)
        if progresso is not None:
            pilula(img, cx0 + p(32), yy + p(4), cw - p(64), p(10), progresso, cor)

    def _rodape_teclas(self, img, x):
        self.texto(img, "D  dados técnicos     Q  sair", x, self.H - 34, 13, "apagado", "SemiBold")

    def espera(self, frame_rgb, rosto_ok=False, landmarks=None):
        return self._tela_etapa(
            frame_rgb, 0, "Aguardando o motorista",
            "Sente-se na posição de dirigir, com o rosto dentro do oval.",
            "info", guia="ok" if rosto_ok else "suave", landmarks=landmarks,
            dicas=("A calibração começa sozinha quando o seu rosto aparecer.",))

    def preparo(self, frame_rgb, restante):
        return self._tela_etapa(
            frame_rgb, 1, "Prepare-se",
            "Sente como se estivesse dirigindo: olhos relaxados, boca fechada, cabeça reta.",
            "info", guia="info", numero=max(1, int(math.ceil(restante))),
            dicas=("A calibração mede os seus olhos e a sua boca em repouso, e os alarmes passam a "
                   "valer para o seu rosto.",))

    def calibrando(self, frame_rgb, progresso, restante):
        return self._tela_etapa(
            frame_rgb, 1, "Calibrando…",
            "Olhe para a frente, como se estivesse dirigindo.",
            "info", guia="info", progresso=progresso,
            dicas=(f"Faltam {_num(max(0.0, restante))} s." if restante > 0 else "Quase lá…",))

    def mensagem(self, titulo, texto, tipo="info", etapa=1):
        """Cartão sobre a última imagem; fica na tela enquanto a voz fala."""
        return self._tela_etapa(None, etapa, titulo, texto, tipo)

    # ------------------------------------------------------------ monitoramento
    def monitor(self, frame_rgb, st, landmarks, t, perclos=0.0, fone=None, fps=None, latencia=None, limiares=None):
        """Tela principal. limiares: o DrowsinessState (para os medidores)."""
        self.ultimo_quadro = frame_rgb
        if self.inicio is None:
            self.inicio = t
        titulo, detalhe, cor, grav = self._classificar(st, t)
        self.linha.registrar(t, grav)
        if fps is not None:
            self._fps = fps if self._fps is None else 0.9 * self._fps + 0.1 * fps
        if latencia is not None:
            self._lat = latencia if self._lat is None else 0.9 * self._lat + 0.1 * latencia
        info = dict(st=st, t=t, perclos=perclos, fone=fone, fps=self._fps, latencia=self._lat,
                    lim=limiares, titulo=titulo, detalhe=detalhe, cor=cor, grav=grav)
        return self._monitor_compacto(frame_rgb, landmarks, info) if self.compacto else \
            self._monitor_amplo(frame_rgb, landmarks, info)

    def _metricas_olho(self, st, lim):
        """(fração da barra, % do olho aberto calibrado, cor) da abertura dos olhos."""
        if not st or st.get("ear_avg") is None or lim is None:
            return None
        base = lim.ear_threshold / core.CLOSED_RATIO
        pct = st["ear_avg"] / base
        cor = "ok" if st["ear_avg"] >= lim.ear_almost_closed else "aviso" if st["ear_avg"] >= lim.ear_threshold else "perigo"
        return min(1.0, pct / 1.25), pct, cor

    @staticmethod
    def _cor_perclos(p, lim):
        """Cinza enquanto a janela não tem os 30 s de dados que o core exige
        para usar o PERCLOS (antes disso um valor alto não dispara nada)."""
        try:
            if lim is not None and lim.perclos_tracker.coverage() < core.PERCLOS_MIN_COVERAGE_SECONDS:
                return "neutro"
        except AttributeError:
            pass
        return "perigo" if p >= core.PERCLOS_ALERT_RATIO else "aviso" if p >= core.PERCLOS_ALERT_RATIO * 0.6 else "ok"

    def _contadores(self, st, lim):
        st = st or {}
        cab = None if (lim is not None and lim.head_pitch_threshold is None) else st.get("head_down_count_recent", 0)
        return [
            ("Piscadas longas", f"{core.LONG_BLINK_WINDOW_SECONDS // 60} min", st.get("blink_count_recent", 0),
             core.LONG_BLINK_FREQ_ALERT),
            ("Bocejos", f"{core.YAWN_WINDOW_SECONDS // 60} min", st.get("yawn_count_recent", 0), core.YAWN_FREQ_ALERT),
            ("Cabeça caindo", f"{core.HEAD_DOWN_WINDOW_SECONDS // 60} min", cab, core.HEAD_DOWN_FREQ_ALERT),
        ]

    def _pontos(self, img, x, y, n, limite, r):
        """n de limite em bolinhas, da direita para a esquerda a partir de x."""
        cor = "perigo" if n >= limite else "aviso" if n > 0 else "trilho"
        for i in range(limite):
            cx = int(x - r - i * (2 * r + self._p(6) if self.compacto else 2 * r + 7))
            if limite - 1 - i < n:
                cv2.circle(img, (cx, y), r, _cor(cor), -1, cv2.LINE_AA)
            else:
                cv2.circle(img, (cx, y), r, _cor("trilho"), 2, cv2.LINE_AA)

    def _som(self, fone):
        if fone is None:
            return "apagado", "Som: sem informação"
        return ("ok", "Fone/caixa Bluetooth") if fone else ("aviso", "Som na saída reserva")

    def _tecnico(self, img, x, y, info):
        st, lim = info["st"] or {}, info["lim"]

        def f(v, c=3):
            return "–" if v is None else _num(v, c)
        linhas = [
            f"EAR  {f(st.get('ear_avg'))}   E {f(st.get('ear_left'))}  D {f(st.get('ear_right'))}",
            f"MAR  {f(st.get('mar'))}   mandíbula {f(st.get('jaw_open'), 2)}",
            f"Pitch  {f(st.get('head_pitch'))}   PERCLOS {info['perclos'] * 100:.0f}%",
        ]
        if lim is not None:
            escala = getattr(lim, "escala_olho", 1.0)
            linhas.append(f"Limiares  fechado {f(lim.ear_threshold)}  semi {f(lim.ear_almost_closed)}")
            linhas.append(f"Ref. olho aberto  {escala * 100:.0f}% do calibrado")
            linhas.append(f"bocejo {f(lim.mar_threshold)}  cabeça "
                          + (f(lim.head_pitch_threshold) if lim.head_pitch_threshold is not None else "desligada"))
        if info["fps"] is not None:
            linhas.append(f"{info['fps']:.0f} fps   IA {info['latencia'] or 0:.0f} ms   nível {st.get('level', '–')}")
        tam = 11 if self.compacto else 14
        passo = int(self._p(tam) * 1.45)
        larg = max(self.txt.largura(l, self._p(tam), "SemiBold") for l in linhas) + self._p(24)
        alt = passo * len(linhas) + self._p(16)
        caixa(img, x, y - alt, larg, alt, "fundo", r=self._p(10), alpha=0.78)
        for i, l in enumerate(linhas):
            self.texto(img, l, x + self._p(12), y - alt + self._p(8) + i * passo, tam, "texto", "SemiBold")

    # posições (y) do painel lateral do monitoramento amplo
    Y_CARTAO, Y_OLHO, Y_PERCLOS, Y_CONT, Y_LINHA = 66, 212, 274, 346, 486

    def _fixo_monitor_amplo(self, cabeca_ligada):
        """Painel lateral com os textos que não mudam, desenhado uma vez."""
        def desenhar(img):
            x, w = self.CAM_W + self.PAINEL_PAD, self.W - self.CAM_W - 2 * self.PAINEL_PAD
            self._cabecalho(img, x)
            self.texto(img, "Abertura dos olhos", x, self.Y_OLHO, 15, "suave", "SemiBold")
            self.texto(img, "PERCLOS · 1 min", x, self.Y_PERCLOS, 15, "suave", "SemiBold")
            for i, (nome, janela, _n, _lim) in enumerate(self._contadores({}, None)):
                yy = self.Y_CONT + i * 40
                lw = self.texto(img, nome, x, yy, 16, "texto", "SemiBold")
                self.texto(img, janela, x + lw + 8, yy + 2, 13, "apagado", "SemiBold")
                if i == 2 and not cabeca_ligada:
                    self.texto(img, "sem calibração", x + w, yy + 1, 13, "apagado", "SemiBold", ax="r")
            self.texto(img, "Últimos 10 minutos", x, self.Y_LINHA, 15, "suave", "SemiBold")
            self.texto(img, "−10 min", x, self.Y_LINHA + 56, 12, "apagado", "SemiBold")
            self.texto(img, "agora", x + w, self.Y_LINHA + 56, 12, "apagado", "SemiBold", ax="r")
            self._rodape_teclas(img, x)
        return self._fixo(("monitor", cabeca_ligada), desenhar)

    def _monitor_amplo(self, frame_rgb, landmarks, info):
        lim = info["lim"]
        img = np.empty((self.H, self.W, 3), np.uint8)
        img[:, self.CAM_W:] = self._fixo_monitor_amplo(lim is None or lim.head_pitch_threshold is not None)[:, self.CAM_W:]
        cam = img[:, :self.CAM_W]
        mapa = self._camera(frame_rgb, cam)
        self._rosto(cam, landmarks, mapa, info["cor"])
        grav = info["grav"]
        if grav >= 3:   # sono ou distração: faixa grande na câmera e borda pulsando
            cor = info["cor"]
            caixa(cam, 0, self.H - 132, self.CAM_W, 132, cor, r=0, alpha=0.88)
            self.texto(cam, "ACORDE!" if grav == 4 else info["titulo"], 36, self.H - 122, 48, "texto", "Black")
            self.texto(cam, info["detalhe"], 38, self.H - 56, 22, "texto", "SemiBold")
            self._borda_alarme(cam, cor)
        elif self.tecnico:
            self._tecnico(cam, 20, self.H - 20, info)

        x, w = self.CAM_W + self.PAINEL_PAD, self.W - self.CAM_W - 2 * self.PAINEL_PAD

        # cartão do estado
        y = self.Y_CARTAO
        cor = info["cor"]
        forte = grav >= 2
        caixa(img, x, y, w, 118, cor if forte else "cartao", r=16,
              alpha=(0.75 + 0.25 * self._pulso()) if grav == 4 else 1.0)
        if not forte:
            cv2.circle(img, (x + 24, y + 34), 9, _cor(cor), -1, cv2.LINE_AA)
        self.texto(img, info["titulo"], x + (20 if forte else 42), y + 16, 30,
                   "fundo" if forte else "texto", "Black", max_w=w - (40 if forte else 62))
        self.texto(img, info["detalhe"], x + 20, y + 70, 17, "fundo" if forte else "suave", "SemiBold", max_w=w - 40)

        # medidores
        y = self.Y_OLHO
        olho = self._metricas_olho(info["st"], lim)
        self.texto(img, f"{olho[1] * 100:.0f}%" if olho else "–", x + w, y, 15, "texto", "Bold", ax="r")
        pilula(img, x, y + 28, w, 10, olho[0] if olho else 0, olho[2] if olho else "trilho")
        if lim is not None:
            for marca in (core.CLOSED_RATIO, core.ALMOST_RATIO):
                mx = int(x + w * marca / 1.25)
                cv2.line(img, (mx, y + 23), (mx, y + 43), _cor("texto"), 2, cv2.LINE_AA)

        y = self.Y_PERCLOS
        p = info["perclos"]
        cor_p = self._cor_perclos(p, lim)
        self.texto(img, f"{p * 100:.0f}%" if cor_p != "neutro" else f"medindo · {p * 100:.0f}%", x + w, y, 15,
                   "texto" if cor_p != "neutro" else "apagado", "Bold", ax="r")
        pilula(img, x, y + 28, w, 10, p / (2 * core.PERCLOS_ALERT_RATIO), cor_p)
        mx = int(x + w * 0.5)
        cv2.line(img, (mx, y + 23), (mx, y + 43), _cor("texto"), 2, cv2.LINE_AA)

        # contadores
        for i, (_nome, _janela, n, limite) in enumerate(self._contadores(info["st"], lim)):
            if n is not None:
                self._pontos(img, x + w, self.Y_CONT + i * 40 + 11, n, limite, 7)

        # linha do tempo
        faixa = self._lut[self.linha.serie() + 1]
        img[self.Y_LINHA + 28:self.Y_LINHA + 50, x:x + w] = cv2.resize(faixa[None], (w, 22),
                                                                       interpolation=cv2.INTER_NEAREST)

        # sistema
        y = self.H - 112
        cor_som, txt_som = self._som(info["fone"])
        cv2.circle(img, (x + 6, y + 10), 6, _cor(cor_som), -1, cv2.LINE_AA)
        self.texto(img, txt_som, x + 20, y, 15, "texto", "SemiBold")
        sistema = f"Sessão {_mmss(info['t'] - self.inicio)}"
        if info["fps"] is not None:
            sistema += f" · {info['fps']:.0f} fps · IA {info['latencia'] or 0:.0f} ms"
        self.texto(img, sistema, x, y + 28, 13, "suave", "SemiBold", max_w=w)
        return img

    def _monitor_compacto(self, frame_rgb, landmarks, info):
        p = self._p
        img = self._quadro()
        grav, cor = info["grav"], info["cor"]
        faixa_h, rodape_h = p(64), p(26)
        meio_h = self.H - faixa_h - rodape_h
        cam_w = min(int(meio_h * 4 / 3), self.W - p(150))
        cam = img[faixa_h:faixa_h + meio_h, :cam_w]
        mapa = self._camera(frame_rgb, cam)
        self._rosto(cam, landmarks, mapa, cor)
        if self.tecnico:
            self._tecnico(cam, p(6), meio_h - p(6), info)

        # faixa do estado: a cor do estado é o principal sinal na telinha
        img[:faixa_h] = np.array(_escurecer(cor, 0.55) if grav <= 1 else _cor(cor), np.uint8)
        letra = "fundo" if cor == "aviso" and grav >= 2 else "texto"
        self.texto(img, info["titulo"], p(14), p(6), 30, letra, "Black", max_w=self.W - p(28))
        self.texto(img, info["detalhe"], p(15), p(41), 14, letra, "SemiBold", max_w=self.W - p(28))

        # coluna da direita: medidores resumidos e contadores
        x, w = cam_w + p(12), self.W - cam_w - p(22)
        y = faixa_h + p(10)
        olho = self._metricas_olho(info["st"], info["lim"])
        self.texto(img, "Olhos", x, y, 13, "suave", "SemiBold")
        self.texto(img, f"{olho[1] * 100:.0f}%" if olho else "–", x + w, y, 13, "texto", "Bold", ax="r")
        pilula(img, x, y + p(21), w, p(7), olho[0] if olho else 0, olho[2] if olho else "trilho")
        y += p(38)
        pc = info["perclos"]
        cor_p = self._cor_perclos(pc, info["lim"])
        self.texto(img, "PERCLOS", x, y, 13, "suave", "SemiBold")
        self.texto(img, f"{pc * 100:.0f}%", x + w, y, 13, "texto" if cor_p != "neutro" else "apagado", "Bold", ax="r")
        pilula(img, x, y + p(21), w, p(7), pc / (2 * core.PERCLOS_ALERT_RATIO), cor_p)
        y += p(42)
        for nome, _janela, n, lim in self._contadores(info["st"], info["lim"]):
            self.texto(img, {"Piscadas longas": "Piscadas", "Cabeça caindo": "Cabeça"}.get(nome, nome),
                       x, y, 13, "texto", "SemiBold")
            if n is None:
                self.texto(img, "–", x + w, y, 13, "apagado", "SemiBold", ax="r")
            else:
                self._pontos(img, x + w, y + p(9), n, lim, p(5))
            y += p(27)

        # rodapé
        y = self.H - rodape_h
        img[y:] = COR["painel"]
        cor_som, txt_som = self._som(info["fone"])
        cv2.circle(img, (p(12), y + rodape_h // 2), p(4), _cor(cor_som), -1, cv2.LINE_AA)
        self.texto(img, "Bluetooth" if info["fone"] else "Som reserva" if info["fone"] is False else "Som",
                   p(22), y + rodape_h // 2, 12, "suave", "SemiBold", ay="m")
        self.texto(img, _mmss(info["t"] - self.inicio), self.W // 2, y + rodape_h // 2, 12, "suave", "Bold",
                   ax="c", ay="m")
        if info["fps"] is not None:
            self.texto(img, f"{info['fps']:.0f} fps", self.W - p(10), y + rodape_h // 2, 12, "apagado", "SemiBold",
                       ax="r", ay="m")

        if grav >= 3:   # sono ou distração: o alarme toma a tela, pulsando entre dois tons
            img[:] = np.array(_escurecer(cor, 0.62 + 0.38 * self._pulso()), np.uint8)
            self.texto(img, "ACORDE!" if grav == 4 else "OLHE PARA", self.W // 2, self.H // 2 - p(28),
                       56 if grav == 4 else 44, "texto", "Black", ax="c", ay="m", max_w=self.W - p(20))
            self.texto(img, info["detalhe"] if grav == 4 else "A ESTRADA", self.W // 2, self.H // 2 + p(34),
                       22 if grav == 4 else 44, "texto", "Black" if grav == 3 else "Bold", ax="c", ay="m",
                       max_w=self.W - p(20))
        return img

    # ------------------------------------------------------------ autoteste
    def autoteste(self, frame_rgb, st, landmarks, t, indice, total, nome, instrucao, restante, duracao,
                  feitos=(), nomes=()):
        """Cenário do autoteste em andamento. feitos: [(nome, passou), ...] dos
        cenários já avaliados; nomes: todos os cenários desta rodada."""
        if frame_rgb is not None:
            self.ultimo_quadro = frame_rgb
        if st is None:   # a voz ainda está falando a instrução
            titulo, detalhe, cor, grav = "OUÇA A INSTRUÇÃO", "O cenário começa quando a voz terminar", "info", -1
        else:
            titulo, detalhe, cor, grav = self._classificar(st, t)
        frac = 1.0 - max(0.0, restante) / duracao if duracao else 0.0
        seg = f"{max(0, math.ceil(restante))} s"
        letra = "fundo" if grav >= 2 else "texto"
        p = self._p
        if self.compacto:
            img = self._quadro()
            faixa_h = p(54)
            mapa = self._camera(frame_rgb, img[faixa_h:], escurecer=0.85)
            self._rosto(img[faixa_h:], landmarks, mapa, cor)
            img[:faixa_h] = np.array(COR["painel"], np.uint8)
            self.texto(img, f"{indice}/{total}  {nome}", p(12), p(6), 20, "texto", "ExtraBold", max_w=self.W - p(80))
            self.texto(img, seg, self.W - p(12), p(6), 20, "info", "Black", ax="r")
            pilula(img, p(12), p(38), self.W - p(24), p(6), frac, "info")
            caixa(img, p(8), self.H - p(40), self.W - p(16), p(32), cor if grav >= 2 else "cartao", r=p(8), alpha=0.9)
            self.texto(img, f"{titulo} · {detalhe}" if detalhe else titulo, self.W // 2, self.H - p(24), 13,
                       letra, "Bold", ax="c", ay="m", max_w=self.W - p(32))
            return img

        img = np.empty((self.H, self.W, 3), np.uint8)
        cam = img[:, :self.CAM_W]
        mapa = self._camera(frame_rgb, cam)
        self._rosto(cam, landmarks, mapa, cor)
        caixa(cam, 20, self.H - 84, 520, 64, cor if grav >= 2 else "painel", r=14, alpha=0.9)
        self.texto(cam, titulo, 40, self.H - 76, 22, letra, "Black", max_w=480)
        self.texto(cam, detalhe, 40, self.H - 46, 15, "fundo" if grav >= 2 else "suave", "SemiBold", max_w=480)

        x, w = self._painel_lateral(img)
        self._cabecalho(img, x)
        self.texto(img, f"AUTOTESTE · CENÁRIO {indice} DE {total}", x, 62, 13, "info", "Bold")
        self.texto(img, nome, x, 82, 28, "texto", "Black", max_w=w)
        y = self.paragrafo(img, instrucao, x, 126, 16, w, "suave", max_linhas=5)
        y = max(y + 12, 250)
        self.texto(img, seg, x, y, 40, "info", "Black")
        pilula(img, x, y + 58, w, 10, frac, "info")
        self._lista_cenarios(img, x, y + 92, w, nomes or [n for n, _ in feitos], indice, feitos)
        self._rodape_teclas(img, x)
        return img

    def _lista_cenarios(self, img, x, y, w, nomes, atual, feitos):
        """Cenários da rodada: ✓/✗ nos já avaliados, ponto no atual."""
        for i, nome in enumerate(nomes):
            cy = y + i * 30
            if i < len(feitos):
                cor, tipo = ("ok", "ok") if feitos[i][1] else ("perigo", "falha")
            elif i + 1 == atual:
                cor, tipo = "info", "atual"
            else:
                cor, tipo = "trilho", None
            self._marca(img, x + 10, cy + 10, 10, tipo, cor)
            self.texto(img, nome, x + 30, cy + 1, 15, "texto" if tipo else "apagado", "SemiBold", max_w=w - 30)

    def _lateral_autoteste(self, titulo, nomes, feitos, atual):
        def desenhar(img, x, w):
            self.texto(img, titulo, x, 62, 13, "info", "Bold")
            self._lista_cenarios(img, x, 92, w, nomes, atual, feitos)
        return desenhar

    def mensagem_autoteste(self, titulo, texto, nomes, feitos=(), atual=0, tipo="info"):
        """Mensagem durante o autoteste (fala da instrução, apresentação), com a
        lista de cenários no painel lateral."""
        return self._tela_etapa(None, 0, titulo, texto, tipo,
                                lateral=self._lateral_autoteste("AUTOTESTE GUIADO", nomes, feitos, atual))

    def resultado_cenario(self, nome, passou, detalhe, nomes=(), feitos=()):
        return self._tela_etapa(None, 0, f"{nome}: {'passou' if passou else 'não passou'}", detalhe,
                                "ok" if passou else "perigo",
                                lateral=self._lateral_autoteste("AUTOTESTE GUIADO", nomes or [nome], feitos, 0))

    def resumo_autoteste(self, resultados):
        """resultados: [(nome, passou, detalhe, ...), ...]"""
        ok = sum(1 for r in resultados if r[1])
        total = len(resultados)
        img = self._quadro()
        p = self._p
        cor = "ok" if ok == total else "aviso"
        if self.compacto:
            self.texto(img, f"{ok} de {total}", self.W // 2, p(14), 40, cor, "Black", ax="c")
            self.texto(img, "cenários passaram", self.W // 2, p(66), 16, "suave", "SemiBold", ax="c")
            for i, r in enumerate(resultados):
                col, lin = i % 2, i // 2
                xx, yy = p(16) + col * (self.W // 2), p(108) + lin * p(46)
                self._marca(img, xx + p(9), yy + p(10), p(9), "ok" if r[1] else "falha", "ok" if r[1] else "perigo")
                self.texto(img, r[0], xx + p(24), yy, 14, "texto", "SemiBold", max_w=self.W // 2 - p(44))
            return img
        self._camera(None, img[:, :self.CAM_W], escurecer=0.35)
        self.texto(img, f"{ok} de {total}", self.CAM_W // 2, self.H // 2 - 70, 96, cor, "Black", ax="c", ay="m")
        self.texto(img, "cenários passaram", self.CAM_W // 2, self.H // 2 + 10, 28, "texto", "Bold", ax="c", ay="m")
        self.texto(img, "Relatório salvo em autoteste_resultados/", self.CAM_W // 2, self.H // 2 + 56, 16, "suave",
                   ax="c", ay="m")
        x, w = self._painel_lateral(img)
        self._cabecalho(img, x)
        self.texto(img, "AUTOTESTE CONCLUÍDO", x, 62, 13, "info", "Bold")
        self._lista_cenarios(img, x, 92, w, [r[0] for r in resultados], total + 1, [(r[0], r[1]) for r in resultados])
        y = 92 + total * 30 + 20
        for r in resultados:
            if not r[1]:
                self.texto(img, r[0], x, y, 14, "perigo", "Bold", max_w=w)
                y = self.paragrafo(img, r[2], x, y + 20, 13, w, "suave", max_linhas=3) + 8
        self._rodape_teclas(img, x)
        return img
