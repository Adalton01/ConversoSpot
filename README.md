# ConversoSpot — Spotify para YouTube Music

O **ConversoSpot** é uma ferramenta gratuita desenvolvida em Python para converter playlists do Spotify em playlists do YouTube Music.

O projeto permite importar músicas por meio de um arquivo CSV, pesquisar as faixas no YouTube Music e adicioná-las a uma nova playlist.

## Como funciona

```text
Playlist do Spotify
        |
        v
Exportação para CSV (Exportify)
        |
        v
ConversoSpot
        |
        v
Pesquisa das músicas no YouTube Music
        |
        v
Criação da playlist
```

## Funcionalidades

- Importação de playlists por arquivos CSV.
- Pesquisa automática de músicas no YouTube Music.
- Interface gráfica para Windows.
- Conversão por linha de comando.
- Acompanhamento do progresso da conversão.
- Registro de músicas não encontradas.
- Geração de arquivos com os resultados.
- Possibilidade de retomar uma conversão interrompida.

## Requisitos

- Python 3.10 ou superior.
- Conta no YouTube Music.
- Conexão com a internet.
- Arquivo CSV exportado do Spotify.
- Windows para utilizar a interface gráfica.

## Instalação

### 1. Clonar o repositório

Abra o terminal e execute:

```bash
git clone https://github.com/Adalton01/ConversoSpot.git
```

### 2. Entrar na pasta do projeto

```bash
cd ConversoSpot
```

### 3. Criar um ambiente virtual

```bash
python -m venv .venv
```

### 4. Ativar o ambiente virtual

No Windows:

```bash
.venv\Scripts\activate
```

### 5. Instalar as dependências

```bash
pip install -r requirements.txt
```

## Configuração do YouTube Music

Na primeira execução, é necessário configurar a autenticação.

Execute:

```bash
ytmusicapi browser
```

Siga as instruções apresentadas no terminal para gerar o arquivo `browser.json`.

Esse arquivo permite que o ConversoSpot acesse sua conta do YouTube Music.

**Importante:** o arquivo `browser.json` contém informações de autenticação. Não compartilhe esse arquivo e nunca o publique no GitHub.

## Preparando a playlist do Spotify

Antes de iniciar a conversão, exporte sua playlist para CSV utilizando o [Exportify](https://exportify.net).

1. Acesse o Exportify.
2. Conecte sua conta do Spotify.
3. Selecione a playlist desejada.
4. Exporte os dados no formato CSV.
5. Utilize o arquivo exportado no ConversoSpot.

## Como utilizar

### Interface gráfica (Windows)

Execute o arquivo:

```bash
python ConversoSpot.pyw
```

Também é possível abrir `ConversoSpot.pyw` com um duplo clique.

Na interface:

1. Selecione o arquivo CSV da playlist.
2. Confira as faixas identificadas.
3. Clique em **Adicionar ao YouTube Music**.
4. Acompanhe o andamento da conversão.

O programa apresenta o progresso da pesquisa e informa o resultado da conversão.

### Linha de comando

Também é possível executar o programa pelo terminal.

**Utilizando o nome do arquivo como nome da playlist:**

```bash
python spotify_para_ytmusic.py "playlist.csv"
```

**Definindo um nome personalizado:**

```bash
python spotify_para_ytmusic.py "playlist.csv" --nome "Rock 90s"
```

**Retomando uma conversão interrompida:**

```bash
python spotify_para_ytmusic.py "playlist.csv" --retomar
```

## Arquivos gerados

| Arquivo | Descrição |
|---|---|
| `browser.json` | Arquivo local de autenticação do YouTube Music. |
| `nao_encontradas.csv` | Lista de músicas que não foram encontradas. |
| `<playlist>_resultado.csv` | Resultado individual de cada faixa. |
| `<playlist>_resultado.txt` | Resumo da conversão e link da playlist. |

## Tratamento de erros e recuperação

O ConversoSpot possui mecanismos para lidar com situações que podem ocorrer durante a conversão:

- Classificação de erros HTTP.
- Tratamento específico para respostas 403 e 429, sem repetição automática.
- Registro do progresso após cada faixa concluída.
- Possibilidade de retomar uma conversão interrompida.
- Identificação de músicas que não foram localizadas.

A disponibilidade da pesquisa depende dos serviços externos e da autenticação do YouTube Music.

## Limitações conhecidas

- Nem todas as músicas do Spotify necessariamente serão encontradas no YouTube Music.
- A correspondência depende dos resultados de pesquisa disponíveis na plataforma.
- Alterações nos serviços externos podem afetar o funcionamento da ferramenta.
- Erros de autenticação, bloqueios HTTP ou indisponibilidade dos serviços podem interromper uma conversão.
- O projeto não sincroniza automaticamente alterações entre as playlists das duas plataformas.

## Tecnologias utilizadas

- Python
- YouTube Music API (ytmusicapi)
- CSV
- Interface gráfica Python

## Segurança

Algumas recomendações importantes:

- Não publique o arquivo `browser.json`.
- Não compartilhe credenciais ou informações de autenticação.
- Utilize o ambiente virtual para instalar as dependências.
- Não envie arquivos pessoais ou dados de sessão para repositórios públicos.

## Objetivo do projeto

O ConversoSpot foi desenvolvido para simplificar a transferência de playlists entre plataformas de música, reduzindo o trabalho manual de localizar e adicionar cada faixa individualmente.

## Autor

**Dalton Souza**

GitHub: [@Adalton01](https://github.com/Adalton01)

---

Projeto independente desenvolvido em Python.
