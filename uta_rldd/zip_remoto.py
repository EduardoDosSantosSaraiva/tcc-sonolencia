"""
Lê arquivos de dentro de um .zip do Google Drive sem baixar o zip inteiro:
o Drive aceita pedidos de trecho (HTTP Range), e o zipfile do Python só
precisa de um arquivo em que dê pra pular de posição. Os zips da UTA-RLDD
têm ~13 GB cada, mais que o espaço livre do cartão do Pi; assim baixa só
os vídeos de uma pessoa por vez.
"""
import io
import time
import zipfile

import requests

URL = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"
TRECHO = 2 << 20   # 2 MB por pedido: pedidos de 8 MB caíam na página de cota excedida


class ArquivoRemoto(io.RawIOBase):
    def __init__(self, file_id, sessao=None):
        self.url = URL.format(id=file_id)
        self.s = sessao or requests.Session()
        r = self.s.get(self.url, headers={"Range": "bytes=0-0"}, timeout=60)
        r.raise_for_status()
        self.tamanho = int(r.headers["Content-Range"].split("/")[1])
        self.pos = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.tamanho}[whence]
        self.pos = base + offset
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.tamanho - self.pos
        n = min(n, self.tamanho - self.pos)
        partes = []
        while n > 0:
            pedaco = self._trecho(self.pos, min(n, TRECHO))
            partes.append(pedaco)
            self.pos += len(pedaco)
            n -= len(pedaco)
        return b"".join(partes)

    def _trecho(self, ini, n):
        """Um pedido de trecho, conferido: o Drive às vezes responde com uma
        página de "Quota exceeded" no lugar dos bytes (foi o que corrompeu
        a primeira tentativa). Nesse caso espera e tenta de novo, com espera
        crescente, em vez de entregar lixo pro zipfile."""
        espera = 15
        while True:
            try:
                r = self.s.get(self.url, headers={"Range": f"bytes={ini}-{ini + n - 1}"}, timeout=120)
                ok = (r.status_code == 206 and len(r.content) == n
                      and r.headers.get("content-type", "").startswith("application/octet-stream"))
                if ok:
                    return r.content
                motivo = "cota do Google excedida" if b"Quota exceeded" in r.content[:4000] else f"HTTP {r.status_code}"
            except requests.RequestException as e:
                motivo = type(e).__name__
            print(f"  [download] {motivo}; nova tentativa em {espera} s", flush=True)
            time.sleep(espera)
            espera = min(espera * 2, 1800)

    def readinto(self, b):
        dados = self.read(len(b))
        b[:len(dados)] = dados
        return len(dados)


def abrir(file_id):
    return zipfile.ZipFile(io.BufferedReader(ArquivoRemoto(file_id), buffer_size=TRECHO))
