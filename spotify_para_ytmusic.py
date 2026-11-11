#!/usr/bin/env python3
"""
spotify_para_ytmusic.py
-----------------------
Converte uma playlist do Spotify (CSV exportado pelo Exportify) em uma
playlist privada do YouTube Music usando a biblioteca ytmusicapi.

Melhorias v2:
  • --seco: simula a conversão sem criar nada no YouTube Music
  • --retomar: continua de onde parou usando um arquivo de progresso JSON
  • Busca inteligente com pontuação por similaridade (difflib)

Uso:
    python spotify_para_ytmusic.py playlist.csv
    python spotify_para_ytmusic.py playlist.csv --nome "Rock 90s"
    python spotify_para_ytmusic.py playlist.csv --seco
    python spotify_para_ytmusic.py playlist.csv --retomar
"""

import argparse
import csv
import difflib
import json
import queue
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import requests

# ---------------------------------------------------------------------------
# Verificação de dependência
# ---------------------------------------------------------------------------
try:
    from ytmusicapi import YTMusic
except ImportError:
    print("❌ Biblioteca 'ytmusicapi' não encontrada.")
    print("   Instale com:  pip install ytmusicapi")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
PASTA_PROJETO         = Path(__file__).resolve().parent
ARQUIVO_AUTH          = PASTA_PROJETO / "browser.json"
ARQUIVO_NAO_ENCONTRADAS = "nao_encontradas.csv"
ARQUIVO_PROGRESSO_TPL = "{stem}_progresso.json"  # Ex.: minha_playlist_progresso.json
TAMANHO_LOTE          = 50     # Limite recomendado pelo ytmusicapi
PAUSA_ENTRE_BUSCAS    = 1.5   # Segundos de espera entre buscas (evita HTTP 403/429 por frequência)
LIMIAR_SIMILARIDADE   = 0.55  # Similaridade mínima (0–1) para aceitar um resultado


class YouTubeMusicResponseError(RuntimeError):
    """Falha HTTP ou de conexão na API do YouTube Music."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        category: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category


class YouTubeMusicSession(requests.Session):
    """Valida respostas YouTubei antes de ytmusicapi tentar decodificá-las."""

    def post(self, url: str, *args, **kwargs) -> requests.Response:
        if "/youtubei/v1/" not in url:
            kwargs.setdefault("timeout", 30)
            return super().post(url, *args, **kwargs)

        kwargs.setdefault("timeout", 30)
        caminho = requests.utils.urlparse(url).path
        retry_seguro = caminho.endswith(("/search", "/browse"))
        tentativas = 5 if retry_seguro else 1
        for tentativa in range(tentativas):
            try:
                resposta = super().post(url, *args, **kwargs)
            except requests.RequestException as erro:
                if tentativa + 1 < tentativas:
                    time.sleep(2.0 * (tentativa + 1))
                    continue
                repeticoes = f"após {tentativas - 1} repetições de conexão" if retry_seguro else "sem repetição"
                raise YouTubeMusicResponseError(
                    f"Falha de rede ao consultar YouTube Music em {caminho} "
                    f"({type(erro).__name__}, {repeticoes}).",
                    category="connection",
                ) from erro
            if (
                resposta.status_code in (403, 429)
                and retry_seguro
                and tentativa + 1 < tentativas
            ):
                espera = min(5.0 * (tentativa + 1), 20.0)
                print(
                    f"   ⏳ YouTube Music recusou temporariamente a requisição "
                    f"(HTTP {resposta.status_code}) em {caminho}; "
                    f"aguardando {espera:.0f}s antes de repetir...",
                    flush=True,
                )
                time.sleep(espera)
                continue
            break

        tipo_conteudo = resposta.headers.get("Content-Type", "ausente")
        status = resposta.status_code
        inicio_corpo = resposta.text.lstrip()[:1]
        if not resposta.content:
            tipo_resposta = "vazia"
        elif (
            "text/html" in tipo_conteudo.lower()
            or resposta.text.lstrip().startswith("<")
        ):
            tipo_resposta = "HTML"
        elif (
            "json" in tipo_conteudo.lower()
            or inicio_corpo in {"{", "["}
        ):
            tipo_resposta = "JSON"
        else:
            tipo_resposta = "não JSON"

        if status == 403:
            raise YouTubeMusicResponseError(
                f"YouTube Music bloqueou ou recusou a requisição (HTTP 403) "
                f"em {caminho}; resposta {tipo_resposta} "
                f"(Content-Type: {tipo_conteudo}; {len(resposta.content)} bytes). "
                "Todas as novas tentativas com espera falharam; aguarde alguns minutos.",
                status_code=status,
                category="blocked",
            )
        if status == 429:
            retry_after = resposta.headers.get("Retry-After")
            detalhe = f" Retry-After: {retry_after}." if retry_after else ""
            raise YouTubeMusicResponseError(
                f"YouTube Music limitou as requisições (HTTP 429) em {caminho}."
                f"{detalhe} Todas as novas tentativas com espera falharam.",
                status_code=status,
                category="rate_limit",
            )
        if status >= 500:
            raise YouTubeMusicResponseError(
                f"Erro do servidor do YouTube Music (HTTP {status}) em {caminho}; "
                f"Content-Type: {tipo_conteudo}.",
                status_code=status,
                category="server",
            )
        if status >= 400:
            raise YouTubeMusicResponseError(
                f"YouTube Music recusou a requisição (HTTP {status}) em {caminho}; "
                f"Content-Type: {tipo_conteudo}.",
                status_code=status,
                category="http",
            )

        try:
            corpo = resposta.json()
        except ValueError as erro:
            raise YouTubeMusicResponseError(
                f"YouTube Music retornou HTTP {status} "
                f"com resposta {tipo_resposta} em {caminho} "
                f"(Content-Type: {tipo_conteudo}; "
                f"{len(resposta.content)} bytes). "
                "A resposta não foi interpretada como JSON.",
                status_code=status,
                category="invalid_response",
            ) from erro

        if not isinstance(corpo, dict):
            raise YouTubeMusicResponseError(
                f"YouTube Music retornou HTTP {status} com JSON em formato "
                f"inesperado em {caminho}.",
                status_code=status,
                category="invalid_response",
            )

        return resposta


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def verificar_autenticacao(seco: bool) -> "YTMusic | None":
    """
    Carrega o arquivo de autenticação.
    Em modo --seco, retorna None sem exigir o arquivo.
    """
    if seco:
        print("🧪 Modo seco ativado — nenhuma ação será realizada no YouTube Music.\n")
        return None

    if not ARQUIVO_AUTH.exists():
        raise FileNotFoundError(
            f"Arquivo de autenticação não encontrado: {ARQUIVO_AUTH}. "
            "Gere-o com 'ytmusicapi browser' e salve-o na pasta do programa."
        )

    try:
        return YTMusic(
            str(ARQUIVO_AUTH),
            requests_session=YouTubeMusicSession(),
        )
    except Exception as e:
        raise RuntimeError(
            f"Não foi possível carregar a autenticação de {ARQUIVO_AUTH}: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Leitura do CSV
# ---------------------------------------------------------------------------

def normalizar_nome_coluna(colunas: list[str], candidatos: list[str]) -> str | None:
    """
    Encontra o nome real de uma coluna aceitando variações de maiúsculas,
    espaços e caracteres especiais.
    """
    colunas_lower = {c.lower().strip(): c for c in colunas}
    for candidato in candidatos:
        if candidato.lower().strip() in colunas_lower:
            return colunas_lower[candidato.lower().strip()]
    return None


def ler_csv(caminho_csv: str) -> list[dict]:
    """
    Lê o CSV exportado pelo Exportify e retorna lista de dicts com
    'titulo', 'artista' e 'artistas_completo'.
    """
    candidatos_titulo = [
        "Track Name", "track name", "Nome da faixa", "Song Name", "Title"
    ]
    candidatos_artista = [
        "Artist Name(s)", "Artist Names", "artist name(s)", "Artist Name",
        "Artists", "artist names", "Artista"
    ]

    musicas = []
    with open(caminho_csv, encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f)
        colunas = leitor.fieldnames or []

        col_titulo = normalizar_nome_coluna(colunas, candidatos_titulo)
        col_artista = normalizar_nome_coluna(colunas, candidatos_artista)

        if not col_titulo:
            raise ValueError(f"Coluna de título não encontrada. Colunas: {colunas}")
        if not col_artista:
            raise ValueError(f"Coluna de artista não encontrada. Colunas: {colunas}")

        for linha in leitor:
            titulo = (linha.get(col_titulo) or "").strip()
            artistas_raw = (linha.get(col_artista) or "").strip()
            if not titulo:
                continue
            primeiro_artista = artistas_raw.split(",")[0].strip()
            musicas.append({
                "titulo": titulo,
                "artista": primeiro_artista,
                "artistas_completo": artistas_raw,
            })

    return musicas


# ---------------------------------------------------------------------------
# Busca inteligente com similaridade
# ---------------------------------------------------------------------------

def _similaridade(a: str, b: str) -> float:
    """
    Retorna índice de similaridade entre duas strings (0.0 a 1.0),
    ignorando maiúsculas/minúsculas e espaços extras.
    """
    return difflib.SequenceMatcher(
        None,
        a.lower().strip(),
        b.lower().strip()
    ).ratio()


def _pontuacao_resultado(item: dict, titulo: str, artista: str) -> float:
    """
    Calcula pontuação de relevância de um resultado de busca.
    Considera similaridade de título e artista separadamente.
    """
    # Extrai título do resultado
    titulo_resultado = (
        item.get("title") or
        item.get("name") or
        ""
    )

    # Extrai artista do resultado (pode vir em formatos diferentes)
    artista_resultado = ""
    artistas = item.get("artists") or []
    if artistas:
        artista_resultado = artistas[0].get("name", "") if isinstance(artistas[0], dict) else str(artistas[0])

    sim_titulo  = _similaridade(titulo,  titulo_resultado)
    sim_artista = _similaridade(artista, artista_resultado) if artista else 0.5

    # Pesos: título tem mais importância que artista
    return sim_titulo * 0.65 + sim_artista * 0.35


def buscar_no_ytmusic(
    ytmusic: YTMusic,
    titulo: str,
    artista: str,
) -> tuple[str | None, float]:
    """
    Busca uma música no YouTube Music com pontuação por similaridade.
    Tenta filter='songs' e, se não houver resultado aceitável, tenta 'videos'.
    Retorna (videoId, pontuação) ou (None, 0.0).
    """
    query = f"{titulo} {artista}".strip()
    melhor_id: str | None = None
    melhor_pontos = 0.0
    erros: list[tuple[str, Exception]] = []

    for filtro in ("songs", "videos"):
        try:
            resultados = ytmusic.search(query, filter=filtro, limit=10)
        except Exception as e:
            erros.append((filtro, e))
            if isinstance(e, YouTubeMusicResponseError) and e.category in {
                "blocked",
                "rate_limit",
            }:
                raise RuntimeError(
                    f"A pesquisa de '{query}' foi interrompida: {e}"
                ) from e
            continue

        if not isinstance(resultados, list):
            erros.append(
                (filtro, RuntimeError("A API retornou resultados em formato inválido."))
            )
            continue

        for item in resultados:
            if not isinstance(item, dict):
                continue
            video_id = item.get("videoId")
            if not video_id:
                continue

            pontos = _pontuacao_resultado(item, titulo, artista)

            if pontos > melhor_pontos:
                melhor_pontos = pontos
                melhor_id = video_id

        # A busca no outro filtro não é necessária quando já existe boa correspondência.
        if melhor_pontos >= LIMIAR_SIMILARIDADE:
            break

    if melhor_id and melhor_pontos >= LIMIAR_SIMILARIDADE:
        return melhor_id, melhor_pontos

    if erros:
        detalhes = "; ".join(
            f"{filtro}: {erro}" for filtro, erro in erros
        )
        raise RuntimeError(
            f"Não foi possível concluir a pesquisa de '{query}'. {detalhes}"
        ) from erros[-1][1]

    return None, melhor_pontos


# ---------------------------------------------------------------------------
# Progresso (retomada de conversão interrompida)
# ---------------------------------------------------------------------------

def caminho_progresso(caminho_csv: str) -> Path:
    stem = Path(caminho_csv).stem
    return Path(caminho_csv).resolve().parent / ARQUIVO_PROGRESSO_TPL.format(stem=stem)


def carregar_progresso(caminho_csv: str) -> dict:
    """Carrega arquivo de progresso existente ou retorna estrutura vazia."""
    arq = caminho_progresso(caminho_csv)
    if arq.exists():
        try:
            with open(arq, encoding="utf-8") as f:
                dados = json.load(f)
            if not isinstance(dados, dict):
                raise ValueError("O arquivo de progresso deve conter um objeto JSON.")
            dados.setdefault("playlist_id", None)
            dados.setdefault("processadas", [])
            dados.setdefault("video_ids", [])
            dados.setdefault("nao_encontradas", [])
            dados.setdefault("resultados", {})
            dados.setdefault("adicionados", [])
            print(f"Progresso anterior carregado de '{arq}'")
            print(f"   Músicas já processadas: {len(dados['processadas'])}\n")
            return dados
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise RuntimeError(
                f"Não foi possível carregar o progresso de '{arq}': {e}. "
                "O arquivo foi preservado; corrija-o ou mova-o antes de continuar."
            ) from e

    return {
        "playlist_id": None,
        "processadas": [],
        "video_ids": [],
        "nao_encontradas": [],
        "resultados": {},
        "adicionados": [],
    }


def salvar_progresso(caminho_csv: str, dados: dict) -> None:
    """Persiste o progresso atual em disco."""
    arq = caminho_progresso(caminho_csv)
    temporario = arq.with_suffix(arq.suffix + ".tmp")
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.flush()
    temporario.replace(arq)


def apagar_progresso(caminho_csv: str) -> None:
    """Remove o arquivo de progresso após conclusão bem-sucedida."""
    arq = caminho_progresso(caminho_csv)
    if arq.exists():
        arq.unlink()
        print(f"Arquivo de progresso '{arq}' removido.")


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def dividir_em_lotes(lista: list, tamanho: int):
    """Divide uma lista em sublistas de tamanho máximo 'tamanho'."""
    for i in range(0, len(lista), tamanho):
        yield lista[i : i + tamanho]


def salvar_nao_encontradas(
    musicas: list[dict],
    caminho_saida: Path | None = None,
) -> None:
    """Salva as músicas não encontradas em um CSV separado."""
    if not musicas:
        return
    destino = caminho_saida or Path(ARQUIVO_NAO_ENCONTRADAS)
    with open(destino, "w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.DictWriter(
            f, fieldnames=["titulo", "artista", "artistas_completo"]
        )
        escritor.writeheader()
        escritor.writerows(musicas)
    print(f"\nMúsicas não encontradas salvas em '{destino}'")


def exportar_resultados(
    caminho_csv: str,
    resultados: list[dict[str, str]],
    link_playlist: str | None,
) -> tuple[Path, Path]:
    """Exporta o resultado completo da conversão em CSV e TXT."""
    origem = Path(caminho_csv)
    caminho_resultado = origem.with_name(f"{origem.stem}_resultado.csv")
    caminho_texto = origem.with_name(f"{origem.stem}_resultado.txt")

    with open(caminho_resultado, "w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=["titulo", "artista", "status", "video_id"],
        )
        escritor.writeheader()
        escritor.writerows(resultados)

    adicionadas = sum(item["status"] == "Adicionada" for item in resultados)
    nao_encontradas = sum(
        item["status"] == "Não encontrada" for item in resultados
    )
    linhas = [
        "Resultado da conversão Spotify → YouTube Music",
        f"Músicas adicionadas: {adicionadas}",
        f"Músicas não encontradas: {nao_encontradas}",
        f"Playlist: {link_playlist or 'não criada'}",
        "",
        "Faixas:",
    ]
    linhas.extend(
        f"- {item['titulo']} — {item['artista']} [{item['status']}]"
        for item in resultados
    )
    caminho_texto.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho_resultado, caminho_texto


def transferir_playlist(
    ytmusic: YTMusic,
    musicas: list[dict],
    nome_playlist: str,
    caminho_csv: str,
    ao_progredir: Callable[[int, int, str], None] | None = None,
    retomar: bool = False,
) -> dict:
    """Pesquisa, salva cada decisão e retoma com segurança se houver falha."""
    total = len(musicas)
    if not retomar and caminho_progresso(caminho_csv).exists():
        raise RuntimeError(
            f"Já existe progresso para este CSV em {caminho_progresso(caminho_csv)}. "
            "Use --retomar para continuar sem perder o checkpoint."
        )
    progresso = carregar_progresso(caminho_csv) if retomar else {
        "playlist_id": None,
        "processadas": [],
        "video_ids": [],
        "nao_encontradas": [],
        "resultados": {},
        "adicionados": [],
    }
    processadas = {int(indice) for indice in progresso["processadas"]}
    progresso["processadas"] = sorted(processadas)
    progresso["video_ids"] = list(dict.fromkeys(progresso["video_ids"]))
    progresso["adicionados"] = list(dict.fromkeys(progresso["adicionados"]))
    progresso["resultados"] = {
        str(indice): resultado
        for indice, resultado in progresso["resultados"].items()
    }
    resultados = progresso["resultados"]
    video_ids = progresso["video_ids"]
    nao_encontradas = list(progresso["nao_encontradas"])
    vistos = set(video_ids)
    musicas_nao_encontradas = {
        (item.get("titulo"), item.get("artista"))
        for item in nao_encontradas
        if isinstance(item, dict)
    }
    for indice in processadas:
        if indice < 0 or indice >= total:
            raise RuntimeError(
                f"O progresso contém índice de música inválido: {indice}."
            )
        chave = str(indice)
        if chave not in resultados:
            musica_antiga = musicas[indice]
            foi_nao_encontrada = (
                musica_antiga["titulo"],
                musica_antiga["artista"],
            ) in musicas_nao_encontradas
            resultados[chave] = {
                "titulo": musica_antiga["titulo"],
                "artista": musica_antiga["artista"],
                "status": "Não encontrada" if foi_nao_encontrada else "Processada",
                "video_id": "",
            }
    salvar_progresso(caminho_csv, progresso)

    def reportar(concluidas: int, mensagem: str) -> None:
        if ao_progredir:
            ao_progredir(concluidas, max(total * 2, 1), mensagem)

    for indice, musica in enumerate(musicas):
        if indice in processadas:
            continue
        titulo = musica["titulo"]
        artista = musica["artista"]
        reportar(len(processadas), f"Pesquisando: {titulo} — {artista}")
        try:
            video_id, _ = buscar_no_ytmusic(ytmusic, titulo, artista)
        except Exception:
            # A faixa permanece pendente; os checkpoints anteriores são mantidos.
            salvar_progresso(caminho_csv, progresso)
            raise

        if video_id and video_id not in vistos:
            vistos.add(video_id)
            video_ids.append(video_id)
            status = "Encontrada"
        elif video_id:
            status = "Duplicada"
        else:
            status = "Não encontrada"
            nao_encontradas.append(musica)

        resultados[str(indice)] = {
            "titulo": titulo,
            "artista": artista,
            "status": status,
            "video_id": video_id or "",
        }
        processadas.add(indice)
        progresso["processadas"] = sorted(processadas)
        progresso["video_ids"] = video_ids
        progresso["nao_encontradas"] = nao_encontradas
        progresso["resultados"] = resultados
        salvar_progresso(caminho_csv, progresso)
        reportar(len(processadas), f"Pesquisadas {len(processadas)} de {total} músicas")
        time.sleep(PAUSA_ENTRE_BUSCAS)

    resultados_ordenados = [
        resultados[str(indice)]
        for indice in sorted(
            (int(i) for i in resultados),
            key=int,
        )
    ]
    nao_encontradas = progresso["nao_encontradas"]

    if not video_ids:
        salvar_nao_encontradas(
            nao_encontradas,
            Path(caminho_csv).with_name(ARQUIVO_NAO_ENCONTRADAS),
        )
        arquivos = exportar_resultados(caminho_csv, resultados_ordenados, None)
        resultado_final = {
            "adicionadas": 0,
            "nao_encontradas": len(nao_encontradas),
            "duplicadas": sum(
                item["status"] == "Duplicada" for item in resultados_ordenados
            ),
            "link": None,
            "arquivos": arquivos,
        }
        apagar_progresso(caminho_csv)
        return resultado_final

    playlist_id = progresso.get("playlist_id")
    playlist_nova = not playlist_id
    if playlist_nova:
        reportar(total, "Criando playlist privada no YouTube Music…")
        playlist_id = ytmusic.create_playlist(
            title=nome_playlist,
            description="Importada do Spotify via ConversoSpot",
            privacy_status="PRIVATE",
        )
        if not isinstance(playlist_id, str) or not playlist_id:
            raise RuntimeError(
                f"O YouTube Music não confirmou a criação da playlist: {playlist_id!r}"
            )
        progresso["playlist_id"] = playlist_id
        salvar_progresso(caminho_csv, progresso)

    link = f"https://music.youtube.com/playlist?list={playlist_id}"

    ids_na_playlist: set[str] = set(progresso["adicionados"])
    if not playlist_nova:
        playlist_existente = ytmusic.get_playlist(playlist_id, limit=None)
        ids_na_playlist.update(
            item.get("videoId")
            for item in playlist_existente.get("tracks", [])
            if item.get("videoId")
        )

    for resultado in resultados.values():
        if resultado["video_id"] in ids_na_playlist:
            resultado["status"] = "Adicionada"
    progresso["adicionados"] = sorted(ids_na_playlist)
    progresso["resultados"] = resultados
    salvar_progresso(caminho_csv, progresso)

    pendentes = [video_id for video_id in video_ids if video_id not in ids_na_playlist]
    for lote in dividir_em_lotes(pendentes, TAMANHO_LOTE):
        ytmusic.add_playlist_items(playlist_id, lote)
        ids_na_playlist.update(lote)
        progresso["adicionados"] = sorted(ids_na_playlist)
        for resultado in resultados.values():
            if resultado["video_id"] in lote:
                resultado["status"] = "Adicionada"
        progresso["resultados"] = resultados
        salvar_progresso(caminho_csv, progresso)
        adicionadas = len(
            {video_id for video_id in video_ids if video_id in ids_na_playlist}
        )
        reportar(total + adicionadas, f"Adicionadas {adicionadas} de {len(video_ids)} músicas")
        time.sleep(1)

    resultados_ordenados = [
        resultados[str(indice)]
        for indice in sorted(
            (int(i) for i in resultados),
            key=int,
        )
    ]
    salvar_nao_encontradas(
        nao_encontradas,
        Path(caminho_csv).with_name(ARQUIVO_NAO_ENCONTRADAS),
    )
    arquivos = exportar_resultados(caminho_csv, resultados_ordenados, link)
    quantidade_adicionadas = sum(
        resultado["status"] == "Adicionada" for resultado in resultados_ordenados
    )
    resultado_final = {
        "adicionadas": quantidade_adicionadas,
        "nao_encontradas": len(nao_encontradas),
        "duplicadas": sum(
            item["status"] == "Duplicada" for item in resultados_ordenados
        ),
        "link": link,
        "arquivos": arquivos,
    }
    apagar_progresso(caminho_csv)
    return resultado_final


class ConversoSpotApp:
    """Interface desktop para importar um CSV e transferir sua playlist."""

    def __init__(self, raiz: tk.Tk) -> None:
        self.raiz = raiz
        self.raiz.title("ConversoSpot — Spotify para YouTube Music")
        self.raiz.geometry("760x580")
        self.raiz.minsize(620, 460)
        self.caminho_csv: str | None = None
        self.musicas: list[dict] = []
        self.eventos: queue.Queue[tuple] = queue.Queue()

        principal = ttk.Frame(raiz, padding=16)
        principal.pack(fill="both", expand=True)
        ttk.Label(
            principal,
            text="Spotify → YouTube Music",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            principal,
            text="Selecione um CSV exportado do Spotify para visualizar e importar as faixas.",
        ).pack(anchor="w", pady=(4, 14))

        barra = ttk.Frame(principal)
        barra.pack(fill="x")
        self.botao_selecionar = ttk.Button(
            barra, text="Selecionar arquivo CSV", command=self.selecionar_csv
        )
        self.botao_selecionar.pack(side="left")
        self.rotulo_arquivo = ttk.Label(barra, text="Nenhum arquivo selecionado")
        self.rotulo_arquivo.pack(side="left", padx=10)

        nome = ttk.Frame(principal)
        nome.pack(fill="x", pady=(12, 8))
        ttk.Label(nome, text="Nome da playlist:").pack(side="left")
        self.entrada_nome = ttk.Entry(nome)
        self.entrada_nome.pack(side="left", fill="x", expand=True, padx=(8, 0))

        colunas = ("numero", "titulo", "artista")
        quadro_lista = ttk.Frame(principal)
        quadro_lista.pack(fill="both", expand=True)
        self.lista = ttk.Treeview(quadro_lista, columns=colunas, show="headings", height=14)
        self.lista.heading("numero", text="#")
        self.lista.heading("titulo", text="Música")
        self.lista.heading("artista", text="Artista")
        self.lista.column("numero", width=48, anchor="center", stretch=False)
        self.lista.column("titulo", width=350)
        self.lista.column("artista", width=260)
        barra_rolagem = ttk.Scrollbar(
            quadro_lista, orient="vertical", command=self.lista.yview
        )
        self.lista.configure(yscrollcommand=barra_rolagem.set)
        self.lista.pack(side="left", fill="both", expand=True)
        barra_rolagem.pack(side="left", fill="y")
        self.rotulo_total = ttk.Label(principal, text="0 músicas identificadas")
        self.rotulo_total.pack(anchor="w", pady=(6, 8))

        self.progresso = ttk.Progressbar(principal, mode="determinate")
        self.progresso.pack(fill="x", pady=(2, 4))
        self.rotulo_status = ttk.Label(principal, text="Selecione um arquivo CSV para começar.")
        self.rotulo_status.pack(anchor="w", pady=(0, 8))

        rodape = ttk.Frame(principal)
        rodape.pack(fill="x")
        self.botao_transferir = ttk.Button(
            rodape,
            text="Adicionar ao YouTube Music",
            command=self.iniciar_transferencia,
            state="disabled",
        )
        self.botao_transferir.pack(side="left")
        self.botao_abrir_link = ttk.Button(
            rodape, text="Abrir playlist", command=self.abrir_playlist, state="disabled"
        )
        self.botao_abrir_link.pack(side="left", padx=8)
        self.link_playlist: str | None = None

    def selecionar_csv(self) -> None:
        caminho = filedialog.askopenfilename(
            title="Selecionar CSV da playlist do Spotify",
            filetypes=[("Arquivos CSV", "*.csv"), ("Todos os arquivos", "*.*")],
        )
        if not caminho:
            return
        try:
            musicas = ler_csv(caminho)
        except (OSError, UnicodeError, csv.Error, ValueError) as erro:
            messagebox.showerror("Não foi possível ler o CSV", str(erro))
            return
        if not musicas:
            messagebox.showwarning(
                "CSV sem músicas", "O arquivo foi lido, mas não contém faixas válidas."
            )
            return

        self.caminho_csv = caminho
        self.musicas = musicas
        self.rotulo_arquivo.configure(text=Path(caminho).name)
        self.entrada_nome.delete(0, "end")
        self.entrada_nome.insert(0, Path(caminho).stem)
        self.lista.delete(*self.lista.get_children())
        for numero, musica in enumerate(musicas, start=1):
            self.lista.insert(
                "", "end", values=(numero, musica["titulo"], musica["artista"])
            )
        self.rotulo_total.configure(text=f"{len(musicas)} músicas identificadas")
        self.rotulo_status.configure(text="CSV carregado. Pronto para transferir.")
        self.botao_transferir.configure(state="normal")
        self.link_playlist = None
        self.botao_abrir_link.configure(state="disabled")

    def iniciar_transferencia(self) -> None:
        if not self.caminho_csv or not self.musicas:
            return
        self.botao_transferir.configure(state="disabled")
        self.botao_selecionar.configure(state="disabled")
        self.botao_abrir_link.configure(state="disabled")
        self.progresso.configure(maximum=max(len(self.musicas) * 2, 1), value=0)
        self.rotulo_status.configure(text="Conectando ao YouTube Music…")
        nome = self.entrada_nome.get().strip() or Path(self.caminho_csv).stem
        threading.Thread(
            target=self._executar_transferencia,
            args=(self.caminho_csv, list(self.musicas), nome),
            daemon=True,
        ).start()
        self.raiz.after(100, self._processar_eventos)

    def _executar_transferencia(
        self,
        caminho_csv: str,
        musicas: list[dict],
        nome: str,
    ) -> None:
        try:
            ytmusic = verificar_autenticacao(seco=False)
            if ytmusic is None:
                raise RuntimeError("A autenticação do YouTube Music não foi carregada.")
            resultado = transferir_playlist(
                ytmusic,
                musicas,
                nome,
                caminho_csv,
                lambda atual, total, texto: self.eventos.put(
                    ("progresso", atual, total, texto)
                ),
                retomar=True,
            )
            self.eventos.put(("concluido", resultado))
        except Exception as erro:
            self.eventos.put(("erro", str(erro)))

    def _processar_eventos(self) -> None:
        ativo = True
        while True:
            try:
                evento = self.eventos.get_nowait()
            except queue.Empty:
                break
            tipo = evento[0]
            if tipo == "progresso":
                _, atual, total, texto = evento
                self.progresso.configure(maximum=total, value=atual)
                self.rotulo_status.configure(text=texto)
            elif tipo == "concluido":
                ativo = False
                resultado = evento[1]
                self.link_playlist = resultado["link"]
                self.botao_abrir_link.configure(
                    state="normal" if self.link_playlist else "disabled"
                )
                self.rotulo_status.configure(
                    text=(
                        f"Concluído: {resultado['adicionadas']} adicionadas; "
                        f"{resultado['nao_encontradas']} não encontradas; "
                        f"{resultado['duplicadas']} duplicadas. "
                        f"Relatórios: {resultado['arquivos'][0].name} e "
                        f"{resultado['arquivos'][1].name}"
                    )
                )
                messagebox.showinfo(
                    "Transferência concluída",
                    f"Músicas adicionadas: {resultado['adicionadas']}\n"
                    f"Não encontradas: {resultado['nao_encontradas']}\n"
                    f"Duplicadas: {resultado['duplicadas']}\n"
                    f"Playlist: {resultado['link'] or 'não criada'}",
                )
            elif tipo == "erro":
                ativo = False
                self.rotulo_status.configure(text="A transferência não foi concluída.")
                messagebox.showerror("Erro na transferência", evento[1])

        if ativo:
            self.raiz.after(100, self._processar_eventos)
        else:
            self.botao_selecionar.configure(state="normal")
            self.botao_transferir.configure(state="normal")

    def abrir_playlist(self) -> None:
        if self.link_playlist:
            webbrowser.open(self.link_playlist)


def abrir_interface() -> None:
    raiz = tk.Tk()
    ConversoSpotApp(raiz)
    raiz.mainloop()


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def main():
    # --- Argumentos de linha de comando ---
    parser = argparse.ArgumentParser(
        description="Converte playlist do Spotify (CSV Exportify) → YouTube Music"
    )
    parser.add_argument(
        "csv",
        metavar="ARQUIVO.CSV",
        nargs="?",
        help="Caminho para o CSV exportado pelo Exportify; sem argumento, abre a interface"
    )
    parser.add_argument(
        "--nome",
        metavar="NOME",
        default=None,
        help="Nome da playlist no YouTube Music (padrão: nome do arquivo CSV)"
    )
    parser.add_argument(
        "--seco",
        action="store_true",
        help="Modo seco: simula a busca sem criar nem modificar nada no YouTube Music"
    )
    parser.add_argument(
        "--retomar",
        action="store_true",
        help="Retoma uma conversão interrompida anteriormente"
    )
    args = parser.parse_args()

    if not args.csv:
        abrir_interface()
        return

    nome_playlist = args.nome or Path(args.csv).stem

    print("=" * 60)
    print("  🎵 Spotify → YouTube Music Converter  v2")
    print("=" * 60)
    print(f"  CSV de entrada   : {args.csv}")
    print(f"  Nome da playlist : {nome_playlist}")
    print(f"  Modo seco        : {'sim' if args.seco else 'não'}")
    print(f"  Retomar          : {'sim' if args.retomar else 'não'}")
    print("=" * 60 + "\n")

    # --- Autenticação ---
    try:
        ytmusic = verificar_autenticacao(args.seco)
        if ytmusic:
            print(f"✅ Autenticação carregada de '{ARQUIVO_AUTH}'")
    except Exception as erro:
        print(f"❌ Erro de autenticação: {erro}")
        sys.exit(1)

    # --- Leitura do CSV ---
    try:
        musicas = ler_csv(args.csv)
    except (OSError, UnicodeError, csv.Error, ValueError) as erro:
        print(f"❌ Erro ao ler o CSV: {erro}")
        sys.exit(1)
    print(f"🎵 {len(musicas)} músicas carregadas do CSV.\n")
    if args.seco:
        for numero, musica in enumerate(musicas, start=1):
            print(f"[{numero}/{len(musicas)}] SIMULADO → {musica['titulo']} - {musica['artista']}")
        print(f"\nSimulação concluída: {len(musicas)} músicas no CSV.")
        return

    def mostrar_progresso(atual: int, total: int, mensagem: str) -> None:
        print(f"[{atual}/{total}] {mensagem}")

    try:
        resultado = transferir_playlist(
            ytmusic,
            musicas,
            nome_playlist,
            args.csv,
            mostrar_progresso,
            retomar=args.retomar,
        )
    except Exception as erro:
        print(f"Transferência interrompida: {erro}")
        print(f"   Progresso preservado em: {caminho_progresso(args.csv)}")
        print("   Para continuar, execute novamente com --retomar.")
        sys.exit(1)

    print("\nTransferência concluída.")
    print(f"  Músicas adicionadas : {resultado['adicionadas']}")
    print(f"  Não encontradas     : {resultado['nao_encontradas']}")
    print(f"  Duplicadas          : {resultado['duplicadas']}")
    print(f"  Playlist            : {resultado['link'] or 'não criada'}")


if __name__ == "__main__":
    main()
