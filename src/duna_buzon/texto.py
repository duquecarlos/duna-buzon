"""HTML body -> plain text, stdlib only (`html.parser`): no script or style
text, whitespace collapsed, truncated to the wire's own 20,000-character
cap on `cuerpo_texto`.
"""

from __future__ import annotations

from html.parser import HTMLParser

LIMITE_CARACTERES = 20_000

_ETIQUETAS_SIN_TEXTO = frozenset({"script", "style"})


class _ExtractorDeTexto(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._fragmentos: list[str] = []
        self._profundidad_omitida = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _ETIQUETAS_SIN_TEXTO:
            self._profundidad_omitida += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _ETIQUETAS_SIN_TEXTO and self._profundidad_omitida > 0:
            self._profundidad_omitida -= 1

    def handle_data(self, data: str) -> None:
        if self._profundidad_omitida == 0:
            self._fragmentos.append(data)

    def texto(self) -> str:
        return "".join(self._fragmentos)


def html_a_texto(html: str) -> str:
    extractor = _ExtractorDeTexto()
    extractor.feed(html)
    extractor.close()
    colapsado = " ".join(extractor.texto().split())
    return colapsado[:LIMITE_CARACTERES]
