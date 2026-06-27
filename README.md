# discord_bot_v2

Bot Discord em Python organizado como pacote instalável, com configuração validada,
logs estruturados, testes automatizados e verificação de qualidade.

## Desenvolvimento local

Requer Python 3.11 ou superior.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
pre-commit install
```

## Conectar ao Discord

1. No Discord Developer Portal, crie uma aplicação e adicione um bot.
2. Na página **Bot**, gere ou copie o token e habilite **Message Content Intent**.
3. Em **OAuth2 > URL Generator**, selecione os escopos `bot` e
   `applications.commands`. Nas permissões, marque ao menos **View Channels**,
   **Send Messages** e **Read Message History**.
4. Abra a URL gerada para adicionar o bot ao seu servidor.
5. Copie `.env.example` para `.env` e substitua `seu_token_aqui` pelo token real.

Nunca compartilhe ou versione esse token. Se ele vazar, redefina-o imediatamente no
Developer Portal.

Com o ambiente virtual ativado, execute:

```powershell
python main.py
```

Quando a conexão estiver pronta, o terminal exibirá `Bot ready`. No Discord, use
`/oi` para testar uma interação nativa. O comando de mensagem `!oi` também continua
disponível quando **Message Content Intent** está habilitado.

Durante o desenvolvimento, os comandos são sincronizados diretamente com cada servidor
conectado para aparecerem imediatamente. O terminal confirma isso com
`Guild commands synchronized`.

## Qualidade e testes

```powershell
ruff check .
ruff format --check .
mypy
pytest
```

O mesmo conjunto roda automaticamente no GitHub Actions em pushes para `main` e em
pull requests. O limiar inicial de cobertura é 60% e deve crescer com os próximos fluxos.

## Fluxo de coleta FDM

1. Crie uma categoria chamada `FARME` no servidor.
   O cargo do bot precisa das permissões **Ver canais**, **Enviar mensagens**,
   **Ler histórico de mensagens** e **Gerenciar canais**.
2. Como administrador, execute `/configurar_bot_fdm` no canal que receberá o painel.
3. No painel, cadastre até 25 produtos e use **Criar canal** para escolher um membro.
4. No canal privado criado, o membro ou um administrador usa **Registrar coleta**.
5. Escolha o produto e informe uma quantidade positiva, aceitando ponto ou vírgula decimal.

Cada pessoa possui somente um canal. Os registros guardam servidor, pessoa beneficiária,
autor do lançamento, indicação de administrador, produto, quantidade e data.

### Banco de dados

O SQLite fica em `data/bot.db` por padrão e é criado automaticamente. Ele é suficiente
para uma única instância do bot e não requer instalação de servidor de banco. Faça backup
do arquivo com o bot desligado. É possível mudar o local usando `DATABASE_PATH` no `.env`.

### Estoque, consultas e metas

- O painel administrativo mostra o total histórico e o estoque atual por produto.
- **Registrar saída** reduz somente o estoque geral e bloqueia valores acima do saldo.
- **Consultar pessoa** aceita `DD/MM/AAAA`; deixe as duas datas vazias para todo o histórico.
- **Definir meta** cria uma campanha para todas as salas. Selecione cada produto, informe
  sua quantidade e finalize para ativar. Uma nova campanha encerra a anterior.
- As salas FARME mostram barras de progresso e permitem ao titular consultar seu período.
- Ao atingir ou ultrapassar todos os produtos, a pessoa aparece como meta batida no painel.

## Estrutura

```text
src/discord_bot_v2/  código da aplicação
tests/               testes automatizados
.github/workflows/   integração contínua
pyproject.toml       dependências e configuração das ferramentas
```

## Evolução para ML/MLOps

Ainda não há treinamento, inferência, dados ou modelos neste repositório. Quando essa
camada for introduzida, separe `src/.../ml/{data,features,training,inference}` e adicione
somente então rastreamento de experimentos, validação/versionamento de dados, registro
de modelos e monitoramento de drift. Isso evita infraestrutura sem uma necessidade real.

Nunca versione `.env`, tokens, datasets privados ou artefatos de modelo grandes.
