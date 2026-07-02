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
python -m discord_bot_v2
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

### Canais de auditoria

Configure dois canais separados com:

```text
/configurar_logs_fdm canal_entradas:#entradas canal_saidas:#saidas
```

Entradas registram pessoa, produto, quantidade e autor. Saídas registram produto,
quantidade e administrador responsável. O bot precisa poder visualizar e enviar mensagens
nos dois canais.

Se uma sala FARME for apagada, seu vínculo é removido automaticamente e a pessoa deixa
de participar da meta ativa. O histórico de coletas permanece preservado no SQLite.

### Caixa e divisão do lucro

O painel administrativo possui controles para **Entrada no caixa**, **Saída do caixa** e
**Reserva da firma**. A moeda padrão é dólar (`USD`) e a reserva inicial é 30%.
O botão **Regra de divisão** permite alternar entre:

- **Proporcional ao progresso**: regra original, na qual a cota é multiplicada pelo
  percentual geral alcançado e o restante permanece no caixa.
- **Faixas + bônus aos 100%**: aplica fatores de 100%, 95%, 85%, 70%, 55%, 40%, 25%
  ou 10%. A sobra das penalidades é dividida somente entre quem bateu 100%; caso ninguém
  cumpra toda a meta, a sobra permanece no caixa.

Ao clicar em **Encerrar meta**, o bot apresenta uma prévia antes da confirmação. Somente
pessoas com sala ativa e alguma coleta no período participam. A prévia identifica a regra
vigente e mostra quanto cada participante receberá antes da confirmação.

Depois da confirmação, o fechamento não pode ser executado novamente para a mesma meta,
o caixa é atualizado e cada sala FARME recebe uma mensagem com o pagamento registrado.

Configure o canal que receberá entradas, despesas e fechamentos do caixa com:

```text
/configurar_log_caixa_fdm canal:#log-caixa
```

O bot precisa das permissões **Ver canal** e **Enviar mensagens** nesse destino.

### Produtos, vendas e relatórios

O catálogo separa produtos de **FARME** e de **VENDA**. Produtos de farme não possuem preço
e são os únicos exibidos nas metas e nas salas privadas. Produtos de venda exigem preço,
recebem estoque manualmente pelo menu administrativo e não aparecem no fluxo de coleta.
O botão **Registrar venda** valida o estoque, registra a saída e adiciona o total ao caixa em
uma única transação. Alterações futuras no preço não mudam vendas antigas.

O botão **Cadastrar produto** pergunta primeiro se o item é de FARME ou VENDA. O botão
**Entrada / saída estoque** aceita qualquer produto e pergunta qual movimentação será feita.
Ao remover um produto, suas coletas e movimentações de estoque deixam de compor os totais;
vendas e movimentações financeiras já concluídas permanecem preservadas.

O painel mantém acesso direto às ações, organizado por linhas: salas e consultas;
produtos e estoque; metas; caixa; vendas e administração. Assim as operações mais usadas
continuam a um clique sem misturar assuntos na mesma linha.

O painel separa o saldo real do caixa do total vendido na semana atual. Em **Relatório de
vendas**, administradores podem consultar o dia, a semana ou o mês atual, com totais por
produto e um gráfico diário em texto.

Ao excluir um produto, ele também é retirado das metas que o utilizam. Coletas, saídas e
vendas antigas mantêm o nome e os valores históricos.

### Consulta e reset do banco

O comando privado `/dados_reset_fdm` mostra um resumo das tabelas do servidor e só pode
ser aberto pelo proprietário da aplicação registrado no Discord Developer Portal. O reset preserva
produtos, preços, salas FARME, painéis, canais de log e configurações, mas apaga coletas,
estoque, metas, vendas e movimentações do caixa. Para executá-lo é necessário ser
administrador, possuir o cargo específico configurado no seletor e digitar a confirmação
exata exibida pelo bot.

## Estrutura

```text
src/discord_bot_v2/  código da aplicação
tests/               testes automatizados
.github/workflows/   integração contínua
pyproject.toml       dependências e configuração das ferramentas
```

## Hospedagem

Use Python 3.11 ou superior. Em uma hospedagem baseada em Git, configure:

```text
Build: pip install -r requirements.txt
Start/Worker: python -m discord_bot_v2
```

O `Procfile` já declara o processo como `worker`, pois bots Discord não são servidores
HTTP. Cadastre `DISCORD_TOKEN`, `DISCORD_INTENTS`, `LOG_LEVEL` e `DATABASE_PATH` como
variáveis de ambiente da plataforma; não envie o arquivo `.env`.

O SQLite precisa de armazenamento persistente. Monte um volume e aponte, por exemplo,
`DATABASE_PATH=/data/bot.db`. Sem volume, algumas hospedagens apagam o banco em cada
reinicialização ou novo deploy. Mantenha apenas uma instância do bot usando esse arquivo.

## Evolução para ML/MLOps

Ainda não há treinamento, inferência, dados ou modelos neste repositório. Quando essa
camada for introduzida, separe `src/.../ml/{data,features,training,inference}` e adicione
somente então rastreamento de experimentos, validação/versionamento de dados, registro
de modelos e monitoramento de drift. Isso evita infraestrutura sem uma necessidade real.

Nunca versione `.env`, tokens, datasets privados ou artefatos de modelo grandes.
