# Segurança

## Administração e credenciais

Servidor padrão em 127.0.0.1, hosts explícitos, sem CORS permissivo.
Argon2id para senhas. Sessões aleatórias em cookie HttpOnly/SameSite Strict; só SHA-256 do token
no banco. Expiração, revogação, troca de senha com senha atual e revogação das outras sessões.
CSRF obrigatório em mutações, Origin validado. Login limitado a cinco falhas/IP/15 minutos,
persistido e serializado por advisory lock; IP somente como hash.

Corpo máximo 150 KB. CSP, nosniff, no framing e saída HTML escapada.
Ownership em tarefas, conversas, memórias, aprovações e capturas.
Todos os usuários criados pela CLI são administradores; não existe RBAC multiusuário neste MVP.
reset-admin é operação local de quem tem acesso ao banco, não uma rota remota.

API key: Fernet no PostgreSQL; chave mestra no .env/ambiente. Portável Windows/Linux,
sem DPAPI nem senha Windows. Proteja .env, .runtime e backups com ACLs e conta de privilégio mínimo.
Dump + chave mestra permitem descriptografar. Rotação da chave exige recriptografar/recadastrar credenciais.
Frontend recebe somente key_configured. Corpos de erro do provedor e raciocínio privado não são registrados.
Redaction mitiga padrões reconhecíveis; não identifica todo segredo possível.

Rede futura: HTTPS, cookie Secure, hosts explícitos e acesso privado autenticado.
Não expor diretamente a API administrativa à internet.

## Policy Engine

Schemas Pydantic estritos, ferramenta habilitada, risco e disponibilidade são verificados fora do modelo.
Depois de aprovação, a permissão e a sessão são revalidadas.

- LOW: leitura autorizada de arquivos/sistema, nomes de janelas e monitor.
- MODERATE: abrir/focar, texto UIA, criação de arquivo e leitura detalhada da janela.
- HIGH: sobrescrita, clique/atalho e captura/envio visual.

Moderado exige confirmação por padrão; auto aprovação é opção explícita.
Alto risco sempre exige decisão para ferramenta/parâmetros exatos, fingerprint e consumo único.
Replay/alteração não são autorizados. Negativa encerra a execução e não é convite ao modelo para contornar.
Parâmetros completos são guardados para revisão concreta; não coloque credenciais neles.
Escrita/digitação de padrões conhecidos de credenciais é bloqueada nesta fase.
Ferramentas de shell/Python arbitrário/exclusão/instalação simplesmente não existem.
Contratos futuros não são anunciados como capacidades disponíveis.

## Arquivos

Escopo padrão data/workspace, configurado em ARGOS_ALLOWED_ROOTS.
Resolve caminhos e rejeita fuga por links/junctions, hard links, UNC, alternate streams,
nomes reservados e arquivos internos/sensíveis reconhecidos.
Leitura regular UTF-8 limitada; escrita somente de formatos de dados, exclusiva por padrão.
Sobrescrita usa temporário e replace atômico com confirmação do conteúdo.

Não é sandbox do sistema operacional: processos locais capazes de trocar diretórios/links
concorrentemente podem explorar TOCTOU. Use ACLs, privilégio mínimo e raízes não controladas
por processos não confiáveis. Não autorize perfil inteiro nem pastas do sistema.

## GUI, imagens e prompt injection

Aplicativos fixos Notepad/Calculadora, sem shell/argumentos livres.
UIA identifica campo/elemento e verifica texto. Atalhos limitados e cliques estruturados requerem aprovação.
Escrita idempotente não envia texto novamente. Conteúdo existente, incluindo espaços/quebras,
exige replace_existing e aprovação HIGH. Falha ValuePattern só permite fallback após leitura
comprovar que o documento não mudou; Win32 usa HWND específico de editor filho, sem clipboard.
Teclado Unicode só para documento vazio/texto curto/foco confirmado, com guarda a cada caractere.
Abrir aplicativo reutiliza janela existente; várias janelas exigem reuse_handle, sem novo lançamento.
expected_process_id pode vincular comandos a processo observado e impedir reutilização de handle.
Falha em ação gráfica interrompe o agente; cliques/atalhos idênticos e ações sem confirmação
não se repetem na mesma tarefa. Leituras têm teto de falhas cumulativo por ferramenta.
Usuário pode mudar desktop durante ação; GUI não é transacional.
Worker/executor revalidam sessão; sem Secure Desktop, AutoAdminLogon ou desbloqueio experimental.

Capturas desativadas por padrão. A aprovação informa envio ao DeepSeek.
Revise a janela: não há detector universal de credenciais em imagens. Capturas têm ID aleatório,
ownership, cache desabilitado e retenção configurável. Banco guarda referência, não imagem.

Observações/documentos/telas são dados não confiáveis no prompt. Política, schemas e limites
nunca dependem da obediência do modelo. Isso reduz impacto, mas não elimina prompt injection
ou interpretação incorreta. Não autorize automaticamente risco moderado em ambientes hostis.

## Cancelamento e retenção

Encerra orquestração/worker e expira aprovações, sem desfazer ações já realizadas.
Escrita curta aguarda término para não ocorrer após declarar cancelamento.
Operações com efeitos colaterais não têm retry automático. Reinício não retoma ações.
Capturas/feed têm retenção; auditoria/histórico/consumo permanecem para backup/arquivamento operacional.
Arquivo de primeiro acesso deve ser removido manualmente após trocar a senha.
