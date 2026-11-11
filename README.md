# 🎵 ConversoSpot — Spotify → YouTube Music

Converte playlists do Spotify em playlists do YouTube Music usando apenas ferramentas gratuitas.

## Como funciona

```
CSV do Exportify → spotify_para_ytmusic.py → Playlist no YouTube Music
```

## Instalação rápida

```bash
pip install -r requirements.txt
```

## Configuração (1ª vez)

```bash
ytmusicapi browser
```

Siga as instruções no terminal para gerar o `browser.json`.

## Uso

### Interface gráfica (Windows)

Abra `ConversoSpot.pyw` com um duplo clique. Na janela, selecione o CSV,
confira as faixas e clique em **Adicionar ao YouTube Music**. O programa mostra
o andamento da pesquisa e a playlist privada criada.
As pesquisas usam a autenticação local de `browser.json`. Erros HTTP são
classificados sem tentar decodificar HTML como JSON; respostas 403 e 429 não são
repetidas automaticamente. O progresso é salvo após cada faixa concluída e a
interface retoma um progresso existente na próxima execução.

Também é possível abrir a interface executando `python spotify_para_ytmusic.py`
sem argumentos.

### Linha de comando

```bash
# Nome da playlist = nome do arquivo CSV
python spotify_para_ytmusic.py "playlist.csv"

# Nome personalizado
python spotify_para_ytmusic.py "playlist.csv" --nome "Rock 90s"

# Retomar uma conversão interrompida
python spotify_para_ytmusic.py "playlist.csv" --retomar
```

## Arquivos gerados

| Arquivo | Descrição |
|---------|-----------|
| `browser.json` | Credenciais do YouTube Music (**não commite!**) |
| `nao_encontradas.csv` | Músicas não encontradas automaticamente |
| `<playlist>_resultado.csv` | Resultado de cada faixa da conversão |
| `<playlist>_resultado.txt` | Resumo legível da conversão e link da playlist |

## Requisitos

- Python 3.10+
- Conta no YouTube Music (gratuita)
- CSV exportado pelo [Exportify](https://exportify.net)
