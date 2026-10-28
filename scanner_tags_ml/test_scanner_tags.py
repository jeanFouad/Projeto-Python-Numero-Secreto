# -*- coding: utf-8 -*-
"""Testes do scanner_tags.py com uma API falsa (sem rede). Rode: python3 -m unittest -v"""
import json
import os
import tempfile
import unittest

import scanner_tags as st

with open(st.CONFIG_PADRAO, encoding="utf-8") as f:
    CONFIG = json.load(f)


def anuncio(mlb, status="active", tags=(), sub=(), permalink=True):
    corpo = {"id": mlb, "title": "Produto " + mlb, "status": status,
             "tags": list(tags), "sub_status": list(sub)}
    if permalink:
        corpo["permalink"] = "https://produto.mercadolivre.com.br/%s-%s-x-_JM" % (mlb[:3], mlb[3:])
    return corpo


class ApiFalsa:
    """Responde às rotas usadas pelo scanner a partir de uma lista de anúncios."""

    def __init__(self, anuncios, moderacoes=None):
        self.anuncios = {a["id"]: a for a in anuncios}
        self.moderacoes = moderacoes or {}
        self.chamadas = []

    def __call__(self, caminho):
        self.chamadas.append(caminho)
        if caminho == "/users/me":
            return 200, {"id": 99, "nickname": "LOJA_TESTE"}
        if caminho.startswith("/users/99/items/search"):
            ids = list(self.anuncios)
            pagina = 1 if "scroll_id=" in caminho else 0
            if "scroll_id=fim" in caminho:
                return 200, {"results": [], "scroll_id": None}
            lote = ids[:100] if pagina == 0 else ids[100:]
            return 200, {"results": lote, "scroll_id": "p2" if pagina == 0 else "fim"}
        if caminho.startswith("/items?ids="):
            ids = caminho.split("ids=")[1].split("&")[0].split(",")
            return 200, [{"code": 200, "body": self.anuncios[i]} for i in ids]
        if caminho.startswith("/moderations/last_moderation/"):
            mlb = caminho.rsplit("/", 1)[1].replace("-ITM", "")
            if mlb in self.moderacoes:
                return 200, [{"name": "x", "wordings": [{"type": "REASON", "value": self.moderacoes[mlb]}]}]
            return 404, {}
        return 404, {}


class TestScanner(unittest.TestCase):

    def setUp(self):
        self.pasta = tempfile.mkdtemp()

    def rodar(self, api, paginas=None):
        baixar = (lambda url: (200, paginas.get(url, ""))) if paginas is not None else None
        return st.escanear(api, CONFIG, self.pasta, baixar=baixar, pausa=0, log=lambda *_: None)

    def test_lista_todos_os_anuncios_com_paginacao_e_lotes(self):
        anuncios = [anuncio("MLB%d" % i) for i in range(1, 151)]
        api = ApiFalsa(anuncios)
        self.rodar(api)
        lotes = [c for c in api.chamadas if c.startswith("/items?ids=")]
        self.assertEqual(len(lotes), 8)                       # 150 anúncios / 20 por lote
        self.assertTrue(any("scroll_id=p2" in c for c in api.chamadas))

    def test_acha_tag_da_api_com_link_e_motivo_oficial(self):
        api = ApiFalsa([anuncio("MLB1", tags=["moderation_penalty", "good_quality_thumbnail"]),
                        anuncio("MLB2")],
                       moderacoes={"MLB1": "Título com dados de contato."})
        achados, _, _ = self.rodar(api)
        self.assertEqual(len(achados), 1)
        a = achados[0]
        self.assertEqual((a["mlb"], a["tag"]), ("MLB1", "moderation_penalty"))
        self.assertIn("MLB-1", a["link"])
        self.assertEqual(a["motivo"], "Título com dados de contato.")
        self.assertEqual(a["tipo_motivo"], "oficial (ML)")

    def test_sem_motivo_oficial_usa_explicacao_da_tag(self):
        achados, _, _ = self.rodar(ApiFalsa([anuncio("MLB1", tags=["poor_quality_picture"])]))
        self.assertEqual(achados[0]["tipo_motivo"], "explicação da tag")

    def test_sub_status_entra_como_penalidade(self):
        achados, _, _ = self.rodar(ApiFalsa([anuncio("MLB1", status="paused", sub=["waiting_for_patch"])]))
        self.assertEqual(achados[0]["tag"], "waiting_for_patch")

    def test_tag_que_so_existe_na_pagina_e_encontrada(self):
        a = anuncio("MLB7")
        paginas = {a["permalink"]: '<script>{"tags":["has_limited_marketplace_visibility"]}</script>'}
        achados, _, _ = self.rodar(ApiFalsa([a, anuncio("MLB8")]), paginas)
        self.assertEqual([(x["mlb"], x["origem"]) for x in achados], [("MLB7", "página do anúncio")])

    def test_tag_achada_na_api_nao_duplica_pela_pagina(self):
        a = anuncio("MLB7", tags=["has_limited_marketplace_visibility"])
        paginas = {a["permalink"]: "has_limited_marketplace_visibility"}
        achados, _, _ = self.rodar(ApiFalsa([a]), paginas)
        self.assertEqual(len(achados), 1)
        self.assertEqual(achados[0]["origem"], "API (tag)")

    def test_para_de_ler_paginas_quando_o_ml_bloqueia(self):
        anuncios = [anuncio("MLB%d" % i) for i in range(1, 11)]
        baixadas = []

        def bloqueado(url):
            baixadas.append(url)
            return 403, ""
        avisos = st.verificar_paginas(anuncios, CONFIG, [], bloqueado, pausa=0)
        self.assertEqual(len(baixadas), 3)
        self.assertIn("bloqueou", avisos[0])

    def test_link_montado_quando_api_nao_traz_permalink(self):
        self.assertEqual(st.link_anuncio(anuncio("MLB123", permalink=False)),
                         "https://produto.mercadolivre.com.br/MLB-123-_JM")

    def test_historico_marca_novo_e_mantem_desde(self):
        caminho = os.path.join(self.pasta, "h.json")
        primeiro = [{"mlb": "MLB1", "tag": "moderation_penalty"}]
        st.aplicar_historico(primeiro, caminho, "2026-10-01")
        segundo = [{"mlb": "MLB1", "tag": "moderation_penalty"}, {"mlb": "MLB2", "tag": "moderation_penalty"}]
        st.aplicar_historico(segundo, caminho, "2026-10-28")
        self.assertEqual((segundo[0]["novo"], segundo[0]["desde"]), (False, "2026-10-01"))
        self.assertEqual((segundo[1]["novo"], segundo[1]["desde"]), (True, "2026-10-28"))

    def test_relatorio_tem_link_clicavel_e_ordem_por_gravidade(self):
        api = ApiFalsa([anuncio("MLB1", tags=["poor_quality_picture"]),
                        anuncio("MLB2", tags=["has_limited_marketplace_visibility"])])
        achados, rel_html, rel_csv = self.rodar(api)
        self.assertEqual(achados[0]["tag"], "has_limited_marketplace_visibility")
        with open(rel_html, encoding="utf-8") as f:
            pagina = f.read()
        self.assertIn("href='https://produto.mercadolivre.com.br/MLB-2-x-_JM'", pagina)
        self.assertLess(pagina.index("Visibilidade limitada"), pagina.index("Fotos de baixa qualidade"))
        with open(rel_csv, encoding="utf-8-sig") as f:
            self.assertIn("https://produto.mercadolivre.com.br/MLB-1-x-_JM", f.read())

    def test_ignora_anuncios_encerrados(self):
        achados, _, _ = self.rodar(ApiFalsa([anuncio("MLB1", status="closed", tags=["moderation_penalty"])]))
        self.assertEqual(achados, [])


if __name__ == "__main__":
    unittest.main()
