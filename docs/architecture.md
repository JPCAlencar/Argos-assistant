# Arquitetura implementada

Monólito modular, um FastAPI/coordenador por banco. PostgreSQL guarda relações e JSONB
guarda schemas, configurações e observações. Binários ficam em disco.

~~~mermaid
flowchart TD
  UI[Painel HTML / JS] --> API[FastAPI + autenticação]
  API --> Tasks[Task Manager]
  Tasks --> Agent[Agent Engine]
  Tasks --> WF[Workflow Engine]
  Agent --> Provider[Provider Manager / DeepSeek]
  Agent --> Exec[Executor + Policy]
  WF --> Exec
  Exec --> Registry[Tool Registry]
  Registry --> Files[Arquivos / sistema]
  Registry --> Windows[Worker Windows]
  Tasks --> PG[(PostgreSQL)]
  Exec --> PG
  PG --> SSE[Eventos SSE]
  SSE --> UI
~~~

| Módulo | Responsabilidade |
|---|---|
| app/main.py | Lifespan, lock exclusivo, recuperação, manutenção e segurança HTTP |
| app/api.py | Endpoints autenticados, ownership, paginação, SSE e administração |
| app/config.py | Settings de inicialização e schemas persistentes |
| app/database.py | SQLAlchemy 2.x, 17 entidades, pool 3 + overflow 1 |
| app/store.py | Configuração, eventos, estados, recuperação e retenção |
| app/tasks.py | Tarefas asyncio, concorrência, cancelamento e timeout geral |
| app/agent.py | Contexto limitado, memória autorizada e ciclo de ferramentas |
| app/providers.py | Protocol independente e adaptador DeepSeek |
| app/executor.py | Validação, política, aprovação exata, timeout, retry, execução e auditoria |
| app/workflows.py | Schema, parâmetros, critérios de sucesso e histórico de etapas |
| app/tools/registry.py | Metadados, schemas, risco, disponibilidade e handlers |
| app/tools/filesystem.py | Escopo, leitura limitada, criação exclusiva e sobrescrita atômica |
| app/tools/system.py | Recursos/processos, sem cmdline ou ambiente |
| app/tools/desktop.py | Contratos Windows e subprocesso sem shell |
| app/tools/desktop_worker.py | UIA, janelas, monitor, teclado, mouse e JPEG |
| app/sessions.py | Desktop de entrada e disponibilidade GUI |
| app/security.py | Argon2id, sessões, CSRF, rate limit, Fernet e auditoria |
| app/integrations.py | Protocols futuros de desktop remoto, sessão, voz, Codex e dispositivos |
| web/ | Jinja2 shell, CSS local e JS sem framework |

## Dados e migrações

Usuários, sessões, tentativas de login, configurações, conversas, mensagens, tarefas,
execuções, aprovações, permissões, workflows, workflow runs, memórias, eventos, auditoria,
uso de modelos e dispositivos. Índices cobrem ownership, estado, cronologia e replay.
Sessões SQLAlchemy são curtas; não ficam abertas durante IA/aprovação.
A migração inicial é snapshot explícito, sem importar modelos mutáveis nas versões.
O aplicativo não cria tabelas silenciosamente.

## Agente

Contexto contém identidade, até 50 workflows resumidos, 20 memórias autorizadas e 12 mensagens recentes,
respeitando limite em caracteres. Observações são limitadas e não confiáveis. Tool calls/resultados
preservam IDs e o reasoning_content exigido em thinking, sem persistir raciocínio privado.

Screenshot aprovada: redimensionamento/compressão JPEG, armazenamento separado e envio multimodal
como image_url em mensagem user. A observação tool é texto. Banco contém referência/dimensões, sem base64.
Capturas e capacidade visual precisam estar habilitadas. Imagens não são enviadas a cada passo.

Reserva conservadora de orçamento antes de chamar; tokens/cache/custo após resposta.
Preço de pico por padrão; não há calendário tarifário. Reserva de imagens não garante faturamento real.
Erros sem usage não permitem reconstruir custo. Não há retry automático de chamada paga em erro de rede.

## Workflows

Executáveis diretamente, sem IA, ou por run_workflow. JSON Schema, substituição literal de valores
{{nome}}, sem eval/código. Timeout/retry/critério por etapa. Retry só para leituras, sem workflows aninhados.
Toda etapa usa o mesmo executor/política.

Recuperação agent_for_low_risk, em execução direta, só encaminha falha técnica em etapa LOW.
Negativas de política não são recuperáveis. Em chamada agêntica, falha técnica vira observação;
permissões continuam fora do modelo. Não há retomada automática de efeitos já realizados.

## Ciclo de vida

PENDING → PLANNING → RUNNING ↔ WAITING_APPROVAL → COMPLETED/FAILED/CANCELLED/TIMED_OUT.
Timeout inclui espera por confirmação. Aprovações terminam com a tarefa.
Reinício marca tarefas ativas FAILED, execuções INTERRUPTED e aprovações EXPIRED.
SSE aceita cursor/Last-Event-ID e revalida sessão. Dashboard/histórico atualizam periodicamente.

## Windows e futuro Linux

Imports Windows ficam no worker. Cada operação roda em processo descartável, serializado,
encerrado ao timeout/cancelamento. Sessão/foco são verificados antes da entrada.
DesktopAdapter reserva execução/capacidades para futura conexão autenticada, sem serviço remoto na v0.1.
No Beelink: pool pequeno, retenção, backups e processamento GUI/áudio no cliente.
Não introduzir infraestrutura distribuída antes de demanda real.
