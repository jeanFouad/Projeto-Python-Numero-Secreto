#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scanner_tags.py — varre TODOS os anúncios da conta no Mercado Livre e aponta os que
têm tags de penalidade, com o link de cada anúncio.

Como funciona:
  1. lista os anúncios da conta        GET /users/{seller}/items/search?search_type=scan
  2. lê as tags em lotes de 20         GET /items?ids=...&attributes=...
  3. abre a página dos anúncios ativos e procura as tags com "buscar_na_pagina"
     (as que só aparecem no código da página, no F12, e não na API)
  4. busca o motivo escrito pelo ML    GET /moderations/last_moderation/{MLB}-ITM
  5. compara com o scan anterior: marca o que é NOVO e desde quando cada tag está lá

Saída: relatório HTML (com link para cada anúncio) e CSV em scanner_tags_ml/relatorios/.
Somente leitura: nunca altera anúncio.

Token (primeiro que existir):
  --token  →  env ML_SELLER_TOKEN  →  cofre do hn-anuncio-ml (~/.hn-anuncio-ml/contas/<seller>.json)
Com várias contas no cofre, escolha com --seller ou env ML_CONTA.

Uso:
  python3 scanner_tags.py
  python3 scanner_tags.py --seller 123456789
  python3 scanner_tags.py --sem-paginas          # só API, mais rápido
"""
import argparse
import csv
import glob
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

ML_API = "https://api.mercadolibre.com"
PASTA = os.path.dirname(os.path.abspath(__file__))
CONFIG_PADRAO = os.path.join(PASTA, "tags_penalidade.json")
SAIDA_PADRAO = os.path.join(PASTA, "relatorios")
COFRE = os.path.expanduser("~/.hn-anuncio-ml/contas")
ATRIBUTOS = "id,title,status,sub_status,tags,permalink"
TAMANHO_LOTE = 20
STATUS_VARRIDOS = ("active", "paused", "under_review")
ORDEM_GRAVIDADE = {"critica": 0, "alta": 1, "media": 2}
NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ---------------------------------------------------------------- acesso à API

def chamar_api(caminho, token):
    """GET na API do ML. Devolve (status_http, corpo_json)."""
    req = urllib.request.Request(ML_API + caminho, headers={
        "Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            bruto = r.read().decode("utf-8")
            return r.status, (json.loads(bruto) if bruto else {})
    except urllib.error.HTTPError as e:
        bruto = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(bruto)
        except ValueError:
            return e.code, {"raw": bruto}


def baixar_pagina(url):
    """Baixa o HTML público do anúncio, como o navegador faz. Devolve (status, texto)."""
    req = urllib.request.Request(url, headers={"User-Agent": NAVEGADOR,
                                               "Accept-Language": "pt-BR,pt;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""


def resolver_token(token, seller):
    """Token por argumento, env ou cofre do hn-anuncio-ml."""
    token = token or os.environ.get("ML_SELLER_TOKEN")
    if token:
        return token
    seller = seller or os.environ.get("ML_CONTA")
    if seller:
        arquivos = [os.path.join(COFRE, "%s.json" % seller)]
    else:
        arquivos = glob.glob(os.path.join(COFRE, "*.json"))
    arquivos = [a for a in arquivos if os.path.exists(a)]
    if not arquivos:
        raise SystemExit("ERRO: nenhuma conta conectada. Passe --token, defina ML_SELLER_TOKEN "
                         "ou conecte a conta pelo hn-anuncio-ml (ml_auth.py login).")
    if len(arquivos) > 1:
        raise SystemExit("ERRO: há %d contas no cofre. Escolha com --seller <id> ou ML_CONTA."
                         % len(arquivos))
    with open(arquivos[0], encoding="utf-8") as f:
        conta = json.load(f)
    if int(conta.get("ml_token_expires_at", 0)) < time.time():
        raise SystemExit("ERRO: o token dessa conta expirou. Renove com: python3 ml_auth.py refresh")
    return conta["ml_seller_token"]


# ---------------------------------------------------------------- coleta

def listar_ids(api, seller):
    """Todos os MLBs da conta, paginando pelo scroll_id (funciona acima de 1.000)."""
    ids, scroll = [], None
    while True:
        caminho = "/users/%s/items/search?search_type=scan&limit=100" % seller
        if scroll:
            caminho += "&scroll_id=" + scroll
        status, corpo = api(caminho)
        if status != 200:
            raise SystemExit("ERRO ao listar anúncios (HTTP %s): %s" % (status, corpo))
        lote = corpo.get("results") or []
        if not lote:
            return ids
        ids.extend(lote)
        scroll = corpo.get("scroll_id")
        if not scroll:
            return ids


def buscar_anuncios(api, ids):
    """Lê título, status, sub_status, tags e link em lotes de 20."""
    anuncios = []
    for i in range(0, len(ids), TAMANHO_LOTE):
        lote = ids[i:i + TAMANHO_LOTE]
        status, corpo = api("/items?ids=%s&attributes=%s" % (",".join(lote), ATRIBUTOS))
        if status != 200:
            raise SystemExit("ERRO ao ler anúncios (HTTP %s): %s" % (status, corpo))
        for resposta in corpo:
            if resposta.get("code") == 200:
                anuncios.append(resposta["body"])
    return [a for a in anuncios if a.get("status") in STATUS_VARRIDOS]


def link_anuncio(anuncio):
    """Link público do anúncio; se a API não trouxer, monta pelo MLB."""
    if anuncio.get("permalink"):
        return anuncio["permalink"]
    mlb = anuncio["id"]
    return "https://produto.mercadolivre.com.br/%s-%s-_JM" % (mlb[:3], mlb[3:])


def novo_achado(anuncio, regra, origem):
    return {"mlb": anuncio["id"], "titulo": anuncio.get("title", ""),
            "status": anuncio.get("status", ""), "link": link_anuncio(anuncio),
            "tag": regra["tag"], "origem": origem}


def encontrar_penalidades(anuncios, config):
    """Cruza tags e sub_status de cada anúncio com a lista configurada."""
    regras_tag = {r["tag"]: r for r in config["tags"]}
    regras_sub = {r["tag"]: r for r in config.get("sub_status", [])}
    achados = []
    for anuncio in anuncios:
        for tag in anuncio.get("tags") or []:
            if tag in regras_tag:
                achados.append(novo_achado(anuncio, regras_tag[tag], "API (tag)"))
        for sub in anuncio.get("sub_status") or []:
            if sub in regras_sub:
                achados.append(novo_achado(anuncio, regras_sub[sub], "API (sub_status)"))
    return achados


def verificar_paginas(anuncios, config, achados, baixar, pausa=1.0, log=print):
    """Procura no HTML da página as tags que a API pode não devolver (as vistas no F12).

    Só anúncios ativos têm página pública. Para se o ML bloquear 3 vezes seguidas.
    Devolve a lista de avisos (lacunas) para o relatório."""
    regras = [r for r in config["tags"] if r.get("buscar_na_pagina")]
    if not regras:
        return []
    ja_achados = {(a["mlb"], a["tag"]) for a in achados}
    ativos = [a for a in anuncios if a.get("status") == "active"]
    bloqueios, falhas, avisos = 0, 0, []
    for n, anuncio in enumerate(ativos, 1):
        if n % 50 == 0:
            log("  páginas verificadas: %d/%d" % (n, len(ativos)))
        try:
            status, texto = baixar(link_anuncio(anuncio))
        except (urllib.error.URLError, OSError):
            status, texto = 0, ""
        if status != 200:
            falhas += 1
            bloqueios = bloqueios + 1 if status in (0, 403, 429) else 0
            if bloqueios >= 3:
                avisos.append("O Mercado Livre bloqueou a leitura das páginas depois de %d de %d "
                              "anúncios ativos. As tags de página não foram verificadas no restante."
                              % (n, len(ativos)))
                return avisos
            continue
        bloqueios = 0
        for regra in regras:
            if regra["tag"] in texto and (anuncio["id"], regra["tag"]) not in ja_achados:
                achados.append(novo_achado(anuncio, regra, "página do anúncio"))
                ja_achados.add((anuncio["id"], regra["tag"]))
        if pausa:
            time.sleep(pausa)
    if falhas:
        avisos.append("%d página(s) de anúncio não puderam ser lidas." % falhas)
    return avisos


def buscar_motivos(api, achados, config):
    """Motivo oficial do ML (texto REASON da última moderação) para as tags marcadas."""
    regras = {r["tag"]: r for r in config["tags"] + config.get("sub_status", [])}
    cache = {}
    for achado in achados:
        regra = regras[achado["tag"]]
        motivo = None
        if regra.get("motivo_oficial"):
            if achado["mlb"] not in cache:
                cache[achado["mlb"]] = ler_moderacao(api, achado["mlb"])
            motivo = cache[achado["mlb"]]
        if motivo:
            achado["motivo"], achado["tipo_motivo"] = motivo, "oficial (ML)"
        else:
            achado["motivo"], achado["tipo_motivo"] = regra["descricao"], "explicação da tag"


def ler_moderacao(api, mlb):
    status, corpo = api("/moderations/last_moderation/%s-ITM" % mlb)
    if status != 200 or not isinstance(corpo, list) or not corpo:
        return None
    for texto in corpo[0].get("wordings", []):
        if texto.get("type") == "REASON" and texto.get("value"):
            return texto["value"]
    return None


# ---------------------------------------------------------------- histórico

def aplicar_historico(achados, caminho, hoje):
    """Marca NOVO e 'desde' comparando com o scan anterior; grava o histórico atual.

    Ajuda a decidir quando refazer: tag que continua lá há muito tempo não vai sair sozinha."""
    anterior = {}
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            anterior = json.load(f)
    atual = {}
    for achado in achados:
        chave = "%s|%s" % (achado["mlb"], achado["tag"])
        achado["novo"] = chave not in anterior
        achado["desde"] = anterior.get(chave, hoje)
        atual[chave] = achado["desde"]
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(atual, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- relatórios

def ordenar(achados, config):
    regras = config["tags"] + config.get("sub_status", [])
    posicao = {r["tag"]: i for i, r in enumerate(regras)}
    gravidade = {r["tag"]: ORDEM_GRAVIDADE.get(r["gravidade"], 9) for r in regras}
    achados.sort(key=lambda a: (gravidade[a["tag"]], posicao[a["tag"]], a["desde"], a["mlb"]))


def gravar_csv(achados, config, caminho):
    regras = {r["tag"]: r for r in config["tags"] + config.get("sub_status", [])}
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["gravidade", "tag", "nome", "mlb", "titulo", "status", "link",
                    "novo", "desde", "motivo", "tipo_motivo", "o_que_fazer", "origem"])
        for a in achados:
            r = regras[a["tag"]]
            w.writerow([r["gravidade"], a["tag"], r["nome"], a["mlb"], a["titulo"], a["status"],
                        a["link"], "sim" if a["novo"] else "não", a["desde"], a["motivo"],
                        a["tipo_motivo"], r["acao"], a["origem"]])


ESTILO = """
:root{--fundo:#f7f7f8;--cartao:#fff;--texto:#1d1d1f;--suave:#6b6b70;--borda:#e3e3e6;
--critica:#c62828;--alta:#e46a00;--media:#a07a00;--novo:#1565c0;--link:#1565c0}
@media (prefers-color-scheme:dark){:root{--fundo:#141416;--cartao:#1e1e21;--texto:#ececef;
--suave:#a0a0a8;--borda:#333338;--critica:#ef5350;--alta:#ffa040;--media:#e0c050;
--novo:#64b5f6;--link:#82b1ff}}
body{margin:0;background:var(--fundo);color:var(--texto);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{margin:0 0 4px}.sub{color:var(--suave);margin:0 0 20px}
.resumo{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:24px}
.chip{background:var(--cartao);border:1px solid var(--borda);border-radius:8px;padding:8px 12px}
.chip b{font-size:20px;margin-right:6px}
section{background:var(--cartao);border:1px solid var(--borda);border-radius:10px;
padding:16px;margin-bottom:18px;border-left:5px solid var(--g)}
.critica{--g:var(--critica)}.alta{--g:var(--alta)}.media{--g:var(--media)}
h2{margin:0;font-size:18px}code{font-size:13px;color:var(--suave)}
.acao{margin:6px 0 12px}.tabela{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px;border-top:1px solid var(--borda);vertical-align:top}
th{color:var(--suave);font-weight:600}a{color:var(--link)}
.novo{background:var(--novo);color:#fff;border-radius:4px;padding:1px 6px;font-size:12px}
.tipo{color:var(--suave);font-size:12px}
.aviso{background:var(--cartao);border:1px solid var(--alta);border-radius:8px;padding:10px 12px;margin-bottom:16px}
"""


def gravar_html(achados, config, conta, avisos, total_anuncios, caminho, quando):
    e = html.escape
    regras = config["tags"] + config.get("sub_status", [])
    por_tag = {}
    for a in achados:
        por_tag.setdefault(a["tag"], []).append(a)
    afetados = len({a["mlb"] for a in achados})
    partes = ["<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
              "<meta name='viewport' content='width=device-width,initial-scale=1'>",
              "<title>Scan de tags</title><style>%s</style></head><body><main>" % ESTILO,
              "<h1>Scan de tags de penalidade</h1>",
              "<p class='sub'>Conta %s · %s · %d anúncios varridos · %d com penalidade</p>"
              % (e(conta), e(quando), total_anuncios, afetados)]
    for aviso in avisos:
        partes.append("<div class='aviso'>%s</div>" % e(aviso))
    if not achados:
        partes.append("<p>Nenhuma das tags configuradas foi encontrada.</p>")
    partes.append("<div class='resumo'>")
    for r in regras:
        if r["tag"] in por_tag:
            partes.append("<span class='chip'><b>%d</b>%s</span>" % (len(por_tag[r["tag"]]), e(r["nome"])))
    partes.append("</div>")
    for r in regras:
        lista = por_tag.get(r["tag"])
        if not lista:
            continue
        partes.append("<section class='%s'><h2>%s · %d anúncio(s)</h2><code>%s</code>"
                      % (e(r["gravidade"]), e(r["nome"]), len(lista), e(r["tag"])))
        partes.append("<p class='acao'><b>O que fazer:</b> %s</p>" % e(r["acao"]))
        partes.append("<div class='tabela'><table><tr><th>Anúncio</th><th>Status</th>"
                      "<th>Desde</th><th>Motivo</th><th>Origem</th></tr>")
        for a in lista:
            selo = " <span class='novo'>NOVO</span>" if a["novo"] else ""
            partes.append(
                "<tr><td><a href='%s' target='_blank' rel='noopener'>%s</a>%s<br>%s</td>"
                "<td>%s</td><td>%s</td><td>%s<br><span class='tipo'>%s</span></td><td>%s</td></tr>"
                % (e(a["link"]), e(a["mlb"]), selo, e(a["titulo"]), e(a["status"]), e(a["desde"]),
                   e(a["motivo"]), e(a["tipo_motivo"]), e(a["origem"])))
        partes.append("</table></div></section>")
    partes.append("</main></body></html>")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(partes))


# ---------------------------------------------------------------- execução

def escanear(api, config, pasta_saida, baixar=None, pausa=1.0, log=print):
    """Roda o scan completo e devolve (achados, caminho_html, caminho_csv)."""
    status, eu = api("/users/me")
    if status != 200:
        raise SystemExit("ERRO: token inválido ou expirado (HTTP %s)." % status)
    seller, conta = str(eu["id"]), eu.get("nickname") or str(eu["id"])
    log("Conta: %s (%s)" % (conta, seller))
    ids = listar_ids(api, seller)
    log("Anúncios na conta: %d" % len(ids))
    anuncios = buscar_anuncios(api, ids)
    log("Anúncios ativos/pausados/em revisão: %d" % len(anuncios))
    achados = encontrar_penalidades(anuncios, config)
    avisos = []
    if baixar:
        log("Verificando as páginas dos anúncios ativos (tags que só aparecem no F12)...")
        avisos = verificar_paginas(anuncios, config, achados, baixar, pausa, log)
    buscar_motivos(api, achados, config)
    os.makedirs(pasta_saida, exist_ok=True)
    agora = datetime.now()
    aplicar_historico(achados, os.path.join(pasta_saida, "historico_%s.json" % seller),
                      agora.strftime("%Y-%m-%d"))
    ordenar(achados, config)
    base = os.path.join(pasta_saida, "scan_tags_%s_%s" % (seller, agora.strftime("%Y%m%d_%H%M")))
    gravar_html(achados, config, conta, avisos, len(anuncios), base + ".html",
                agora.strftime("%d/%m/%Y %H:%M"))
    gravar_csv(achados, config, base + ".csv")
    for aviso in avisos:
        log("AVISO: " + aviso)
    return achados, base + ".html", base + ".csv"


def main():
    p = argparse.ArgumentParser(description="Varre os anúncios da conta atrás de tags de penalidade.")
    p.add_argument("--token", help="access_token do ML (senão usa ML_SELLER_TOKEN ou o cofre)")
    p.add_argument("--seller", help="seller_id da conta no cofre, quando há mais de uma")
    p.add_argument("--config", default=CONFIG_PADRAO, help="lista de tags (JSON)")
    p.add_argument("--saida", default=SAIDA_PADRAO, help="pasta dos relatórios")
    p.add_argument("--sem-paginas", action="store_true",
                   help="não abre as páginas dos anúncios (só o que a API devolve)")
    p.add_argument("--pausa", type=float, default=1.0,
                   help="segundos entre uma página e outra (padrão 1)")
    a = p.parse_args()
    with open(a.config, encoding="utf-8") as f:
        config = json.load(f)
    token = resolver_token(a.token, a.seller)
    achados, rel_html, rel_csv = escanear(lambda caminho: chamar_api(caminho, token), config,
                                          a.saida, None if a.sem_paginas else baixar_pagina, a.pausa)
    print("\nEncontrados: %d tag(s) em %d anúncio(s)." % (len(achados), len({x["mlb"] for x in achados})))
    for x in achados:
        print("  [%s] %s %s %s" % (x["tag"], x["mlb"], "NOVO" if x["novo"] else "desde " + x["desde"], x["link"]))
    print("\nRelatório: %s\nPlanilha:  %s" % (rel_html, rel_csv))


if __name__ == "__main__":
    sys.exit(main())
