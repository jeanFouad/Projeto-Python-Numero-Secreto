# Scanner de tags de penalidade (Mercado Livre)

Varre **todos os anúncios da conta** e aponta os que têm tags de penalidade, com o
**link de cada anúncio**, o motivo e o que fazer. Somente leitura: nunca altera anúncio.

## O que ele faz

1. Lista todos os anúncios da conta (ativos, pausados e em revisão).
2. Lê as tags e o `sub_status` de cada um pela API, em lotes de 20.
3. Abre a página dos anúncios ativos e procura as tags que só aparecem no código da
   página (F12), como a `has_limited_marketplace_visibility`.
4. Busca o motivo escrito pelo Mercado Livre na moderação do anúncio, quando existe.
5. Compara com o scan anterior: marca o que é **NOVO** e mostra **desde quando** cada
   tag está no anúncio. Tag que não sai há muito tempo é sinal de que vale refazer o anúncio.

## Como rodar

Precisa de Python 3 (sem bibliotecas extras) e de uma conta conectada:

```bash
cd scanner_tags_ml
python3 scanner_tags.py                     # usa a conta do cofre do hn-anuncio-ml
python3 scanner_tags.py --seller 123456789  # quando há mais de uma conta
python3 scanner_tags.py --token APP_USR-... # ou com um access_token direto
python3 scanner_tags.py --sem-paginas       # só API, sem abrir as páginas (mais rápido)
```

O resultado sai em `scanner_tags_ml/relatorios/`:

- `scan_tags_<seller>_<data>.html`: relatório agrupado por tag, do mais grave para o menos
  grave, com link clicável para cada anúncio;
- `scan_tags_<seller>_<data>.csv`: a mesma lista em planilha (abre no Excel);
- `historico_<seller>.json`: usado para marcar o que é novo no próximo scan.

## Incluir ou mudar tags

Edite `tags_penalidade.json`. Cada tag tem nome, gravidade (`critica`, `alta`, `media`),
explicação, o que fazer, se deve ser procurada na página (`buscar_na_pagina`) e se vale
buscar o motivo oficial na moderação (`motivo_oficial`).

## Testes

```bash
python3 -m unittest -v
```
