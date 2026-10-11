# Validação do MVP 0.1 — 11/10/2026

Ambiente efetivamente utilizado: Windows, Python 3.13, PostgreSQL portátil 17.11 em
127.0.0.1:55432, banco argos_test separado, Notepad 11.2607.14.0 e Chromium headless.

| Verificação | Resultado | O que foi real / simulado |
|---|---|---|
| Unitários | 10 aprovados | Validação, escopo de arquivos, criptografia e contrato HTTP; HTTP simulado |
| Integração backend | 26 aprovados | PostgreSQL, APIs, arquivos, estados e workflows reais; respostas IA e GUI simuladas |
| Integração Windows | 1 aprovado | Notepad, UIA, fallback Win32, texto Unicode e quebra de linha reais; decisão IA simulada |
| Painel | 7 verificações aprovadas | Login, navegação, workflow, SSE, erro sem chave, mobile e ausência de erro JS no Chromium |
| Migração | Alembic check aprovado | Schema aplicado no PostgreSQL, sem operações pendentes |
| Qualidade | Ruff aprovado | app, scripts, tests e migrations |

## Automação gráfica

A causa das múltiplas janelas era a reexecução do teste sem propriedade/limpeza. A antiga
detecção retornava janela anterior imediatamente após lançar outro Notepad. Ctrl+N podia
adicionar abas a uma janela existente, e as ações malsucedidas eram reexecutadas em novas rodadas.

A primeira tentativa do novo harness constatou que abrir um arquivo vazio reutilizava uma
janela existente. Parou sem digitar; a aba UUID criada foi fechada separadamente. A preparação
foi corrigida para usar uma aba descartável como origem de Ctrl+Shift+N, identificar a janela
nova, exigir um único editor vazio e remover a aba inicial. Não altera configurações do Notepad.
O [atalho de nova janela é documentado pela Microsoft](https://blogs.windows.com/windows-insider/2018/12/10/announcing-windows-10-insider-preview-build-18298/).

Na execução aprovada:

- Um lançamento inicial e uma solicitação de janela nova, sem retries de escrita.
- Texto de 55 caracteres, Unicode e uma quebra de linha confirmado por UIA.
- ValuePattern indisponível: fallback WM_SETTEXT em editor filho específico, leitura antes/depois.
- Segunda solicitação do mesmo texto retornou already_satisfied, sem ação.
- open_application com reuse_handle retornou a janela própria, sem lançamento.
- Outro texto sobre o documento preenchido foi recusado, preservando o texto anterior.
- Texto do teste descartado e fechamento da janela própria confirmado.
- As 10 janelas anteriores permaneceram abertas. Nenhum arquivo preexistente foi salvo ou alterado.

[WM_SETTEXT](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-settext) e
[SendMessageTimeoutW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)
fundamentam o fallback dirigido e com prazo. O relatório local fica em
test-results/windows-integration.json; há também relatório por UUID, sem conteúdo digitado.

Regressões simuladas verificam que falha gráfica encerra após uma ação, comando incerto não
é reenviado, e repetição de falha de leitura respeita o teto de tentativas mesmo em novas chamadas da IA.

## Limites da validação

Não houve chamada paga ao DeepSeek: chave ausente. Tool calling, reasoning, custo/cache e
payload visual foram testados com respostas HTTP/modelo simuladas. A análise visual real por
IA e comandos naturais completos ainda precisam de chave e validação do proprietário.

Escrita real foi comprovada somente neste Notepad. Fallback de teclado Unicode e mouse têm
guardas e limites, mas não foram exercitados em integração real; não são declarados aprovados.
Atalhos genéricos/cliques retornam confirmação desconhecida quando não há critério verificável.
Mudança de foco/documento pelo usuário pode interromper operações. Cancelamento não desfaz efeitos.

Os testes não encerram janelas anteriores: podem conter documentos do usuário, inclusive abas
restauradas ou deixadas pelas versões antigas do teste. Não há limpeza por nome de processo.
Não foram testados bloqueio/login/UAC, remoto, Linux, restauração de backup ou aplicativos corporativos.
Voz, Codex CLI, dispositivos e desbloqueio remoto permanecem contratos/roadmap.

Capturas locais do painel: test-results/login.png, dashboard-desktop.png, workflow-chat.png,
dashboard-mobile.png e chat-mobile.png. Relatório: test-results/ui-smoke.json.

Comandos de execução e separação das suítes estão no [README](../README.md).
