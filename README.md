# ARGOS 0.1

Assistente pessoal local em português brasileiro: FastAPI, PostgreSQL, agente próprio com
tool calling, workflows determinísticos e painel responsivo. Toda ferramenta passa por
schema, permissões, aprovação aplicável, limites e auditoria.

## Workspace preparado

Python 3.13 em **.venv**, PostgreSQL 17.11 portátil em **.runtime/pgsql**, dados em
**.runtime/pgdata**, porta **55432**, migração aplicada e rotina **Diagnóstico local** cadastrada.
Nenhum serviço Windows foi instalado; o banco escuta somente em 127.0.0.1.

Administrador: **admin**. A senha aleatória está em **.runtime/first-access.txt**.
Entre, altere a senha em **Segurança** e remova esse arquivo manualmente.
A chave DeepSeek ainda precisa ser cadastrada. O aplicativo informa erro real quando ela está ausente.

Para iniciar novamente, no PowerShell a partir desta pasta:

~~~powershell
.\scripts\start.ps1
~~~

Abra **http://127.0.0.1:8000**. Se já houver servidor ativo, utilize o painel existente.
Se a política do PowerShell impedir execução do script, não é necessário alterá-la:

~~~powershell
.\.venv\Scripts\python.exe scripts/local_postgres.py start
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
~~~

Um lock PostgreSQL permite apenas um coordenador por banco, protegendo recuperação e execução.

## Instalação

Pré-requisitos: Python 3.12/3.13, PostgreSQL 16+ e sessão Windows interativa para ferramentas GUI.
O core e as ferramentas de arquivos/sistema são portáveis para Linux.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,desktop]"
~~~

As versões exatas verificadas no Windows/Python 3.13 ficam em **requirements.lock**:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
~~~

No Linux use o extra **dev**, sem desktop, e **.venv/bin/python**. A instalação atual é por
checkout; templates, migrações e scripts acompanham a pasta do projeto. Empacotamento wheel
independente com assets web ainda não está implementado.

O backend usa asyncpg, validado com o loop padrão do Windows e subprocessos assíncronos.
Psycopg síncrono é utilizado somente pelo auxiliar de preparação, fora do servidor.

### PostgreSQL

Escolha uma opção:

1. **Portátil no Windows:** execute
   **.\.venv\Scripts\python.exe scripts/local_postgres.py setup**.
   Baixa ZIP oficial EDB, verifica SHA-256, extrai somente componentes do banco, configura
   SCRAM e cria roles sem superpoderes para argos/argos_test. Grava segredos aleatórios no .env.
   Não registra serviços e não sobrescreve configuração existente.
2. **Instalação existente:** com uma conta administrativa em psql:

   ~~~sql
   CREATE ROLE argos LOGIN PASSWORD 'substitua_por_senha_forte';
   CREATE DATABASE argos OWNER argos;
   CREATE ROLE argos_test LOGIN PASSWORD 'outra_senha_forte';
   CREATE DATABASE argos_test OWNER argos_test;
   ~~~

   Execute **python -m app.cli configure** e informe uma URL como
   **postgresql+asyncpg://argos:SENHA_URL_ENCODED@127.0.0.1:5432/argos**.
   Caracteres especiais na senha precisam de URL encoding.
3. **Docker já disponível:** defina POSTGRES_PASSWORD no ambiente, execute
   **docker compose up -d db** e configure o .env como acima. Docker é opcional.

Com o banco acessível:

~~~powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.cli seed
.\.venv\Scripts\python.exe -m app.cli create-admin
~~~

A senha é solicitada sem eco. A opção **--generate** salva senha aleatória no arquivo de primeiro
acesso local, ignorado pelo Git. **reset-admin** permite recuperação pelo operador local e revoga
sessões anteriores; não é uma API remota.

O **.env.example** descreve a inicialização. Proteja .env, .runtime e backups com permissões do
sistema operacional. A chave mestra deve ter backup separado.

### DeepSeek

Em **Modelos**, cadastre a API key, base https://api.deepseek.com, modelo deepseek-flash, capacidades
e limites. A chave é criptografada com Fernet antes de ir ao banco e não é devolvida ao navegador.
**Testar conexão real** faz uma chamada de até 128 tokens e pode consumir saldo.

Os preços iniciais usam a tarifa de pico consultada em 11/10/2026: USD por milhão de tokens,
entrada 0.30, cache 0.006, saída 1.20. São configuráveis. Não há calendário automático de feriados
chineses/horários tarifários. A estimativa não substitui cobrança oficial.
[Preços oficiais](https://api-docs.deepseek.com/quick_start/pricing/).

## Painel

- Dashboard: status, modelo, computador, tarefas, consumo e alertas.
- Chat: conversas persistentes, ações via SSE, aprovações e cancelamento.
- Modelos: chave, parâmetros, capacidades e teste real de conexão.
- Ferramentas: descrição/schema, risco, GUI/background e ativação.
- Automações: cadastro JSON validado, execução, passos e histórico.
- Dispositivos: computador local, sessão e capacidades.
- Memória: preferências autorizadas, edição e remoção.
- Segurança: decisões, auditoria, revogação de outras sessões e troca de senha.
- Configurações: personalidade, limites, orçamento, retenção e política.
- Histórico: ações observadas, erros, resultado e consumo por tarefa.

## Como testar o agente

1. Sem chave: **Automações → Diagnóstico local**. Executa consultas reais sem IA.
2. Cadastre a chave e envie: “Olá, Argos. O que você consegue fazer?”.
3. Envie: “Abra o Bloco de Notas”. Aprove a chamada específica.
4. Envie: “Abra o Bloco de Notas e escreva uma explicação curta sobre inteligência artificial”.
   O agente seleciona ferramentas em várias iterações. Aplicativos abertos são reutilizados;
   se houver várias janelas, precisa escolher um handle observado. O Notepad pode restaurar
   documentos anteriores: aprove a criação de documento novo antes de escrever.
5. Envie: “Analise a janela ativa”. Para visão, habilite capturas nas Configurações e aprove
   individualmente a captura e o envio ao DeepSeek.
6. Crie um texto em data/workspace e peça leitura/criação de outro arquivo. Sobrescrita sempre
   exige aprovação de alto risco, mesmo com autorização moderada automática.
7. Negue uma ação ou cancele uma tarefa. O agente encerra sem contornar a decisão.

Cancelamento não desfaz ações já realizadas. Revise resultados desconhecidos antes de repetir.
Não há ferramentas de shell, exclusão, instalação ou manipulação de credenciais.

## Testes

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -m unit
.\.venv\Scripts\python.exe -m pytest -q -m "integration and not desktop"
.\.venv\Scripts\ruff.exe check app scripts tests migrations
.\.venv\Scripts\python.exe -m alembic check
~~~

Chamadas DeepSeek são simuladas nos testes; nenhuma chamada paga é feita automaticamente.
Integração usa PostgreSQL real exclusivo: **.runtime/test-database-url.txt** ou
**ARGOS_TEST_DATABASE_URL**. O nome do banco deve começar com **argos_test**.
**Os testes truncam suas tabelas.** Sem esse banco, testes de integração são explicitamente pulados.
SQLite não é utilizado.

### Integração gráfica real, separada e opt-in

~~~powershell
$env:ARGOS_RUN_DESKTOP_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -q -m desktop tests/test_desktop_windows.py --maxfail=1
Remove-Item Env:ARGOS_RUN_DESKTOP_TESTS
~~~

A suíte padrão exclui desktop mesmo com essa variável definida. A seleção **-m desktop** e
a variável são necessárias. Não execute simultaneamente com outras automações gráficas.
O teste cria uma aba vazia identificada por arquivo UUID dentro de test-results, solicita
uma janela separada uma vez, remove a aba inicial e verifica janela/processo/editor próprios.
Só escreve no documento novo dessa janela. Verifica texto, idempotência, reutilização e recusa
de substituição; descarta o próprio texto, fecha somente a própria janela e compara as janelas
anteriores. Não salva o texto digitado nem encerra processos Notepad compartilhados.

Cada ação com efeito colateral tem uma tentativa. Leituras respeitam max_attempts, inclusive
quando a IA tenta solicitar a mesma ferramenta após falha. Falha gráfica encerra a tarefa;
ação sem confirmação e clique/atalho já enviado não podem ser repetidos automaticamente.
Logs estruturados por execução e resumo **test-results/windows-integration.json** registram
etapa, método, identidade, duração e confirmação, sem registrar conteúdo digitado.
Se a propriedade/limpeza não puder ser comprovada, o teste falha e preserva conteúdo desconhecido.

Smoke opcional da interface:

~~~powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts/smoke_ui.py
~~~

Usa a senha temporária da instalação e pressupõe ausência da chave de IA. Executa diagnóstico,
verifica SSE, navegação e mobile. Depois de configurar chave/trocar senha, adapte o teste para
ambiente de desenvolvimento isolado. Capturas/relatório ficam em **test-results/**.
Resultados efetivamente verificados: [docs/validation.md](docs/validation.md).

## Limitações

- IA DeepSeek real e GUI de outros aplicativos precisam de validação; não há chave configurada.
- Aplicativos permitidos inicialmente: Notepad e Calculadora. Sem argumentos livres.
- Escrita usa UIA; sem ValuePattern tenta WM_SETTEXT somente em editor Win32 inequívoco,
  com leitura antes/depois. Último fallback de teclado exige documento vazio, texto até 256
  caracteres e foco comprovado. Esse último caminho e fallback de mouse ainda não foram
  validados em integração real; falham sem repetição se o estado não puder ser confirmado.
- Clique por automation_id, sem coordenadas fixas. Atalhos limitados exigem aprovação.
- GUI não executa em bloqueio/login/UAC/Session 0/desktop desconhecido.
- Estado local não distingue com certeza login, bloqueio e UAC ou energia de máquinas remotas.
- Prompt injection é mitigado, não eliminado. O modelo pode interpretar observações incorretamente.
- Não há detector universal de dados sensíveis em screenshots; revise a janela antes de autorizar envio.
- Timeout/cancelamento do worker não reverte entradas já enviadas. Escrita curta aguarda conclusão
  antes de finalizar cancelamento, podendo ultrapassar brevemente o timeout.
- Um worker; reinício marca tarefas interrompidas como FAILED, sem retomada de ações.
- Retenção automática de capturas/feed; histórico/auditoria/consumo requerem arquivamento operacional.
- Progresso via SSE, sem streaming de tokens do modelo.
- Voz, Codex, Wake-on-LAN, remoto, ESP32 e desbloqueio remoto têm contratos/roadmap, não ferramentas.

## Backup e restauração

Use pg_dump com PGPASSFILE protegido ou prompt, sem expor senha no comando/logs.
Utilitários portáteis ficam em **.runtime/pgsql/bin**.

~~~powershell
pg_dump -h 127.0.0.1 -p 55432 -U argos -d argos -Fc -f argos-backup.dump
~~~

Faça backup separado da chave ARGOS_MASTER_KEY/.env, arquivos autorizados e capturas se necessário.
Sem chave mestra, as credenciais criptografadas não podem ser recuperadas. Não copie pgdata em uso
como substituto de um backup consistente.

Com ARGOS parado, restaure em **banco novo**, criado com ownership correto pelo administrador:

~~~powershell
pg_restore -h 127.0.0.1 -p 55432 -U argos -d argos_restored --no-owner argos-backup.dump
~~~

Aponte a URL para esse banco, preserve a chave e execute alembic upgrade head.
Se migrar sem a chave, recadastre o provedor. Valide restore em ambiente separado.
Para parar o banco portátil: **python scripts/local_postgres.py stop**, após parar o servidor.

[Arquitetura](docs/architecture.md) · [Segurança](docs/security.md) ·
[Sessões Windows](docs/windows-sessions.md) · [Roadmap](docs/roadmap.md).
