from __future__ import annotations

from duna_buzon.texto import LIMITE_CARACTERES, html_a_texto


def test_etiquetas_simples_se_convierten_a_texto() -> None:
    assert html_a_texto("<p>Hola <b>mundo</b></p>") == "Hola mundo"


def test_texto_de_script_y_style_se_descarta() -> None:
    html = "<p>visible</p><script>var x = 'oculto';</script><style>.a{color:red}</style>"
    resultado = html_a_texto(html)
    assert "oculto" not in resultado
    assert "color" not in resultado
    assert resultado == "visible"


def test_espacios_en_blanco_se_colapsan() -> None:
    html = "<p>line one</p>\n\n   <p>line   two</p>"
    assert html_a_texto(html) == "line one line two"


def test_texto_se_trunca_a_20000_caracteres() -> None:
    html = "<p>" + ("a" * 30_000) + "</p>"
    resultado = html_a_texto(html)
    assert len(resultado) == LIMITE_CARACTERES
    assert resultado == "a" * LIMITE_CARACTERES


def test_html_vacio_da_texto_vacio() -> None:
    assert html_a_texto("") == ""
