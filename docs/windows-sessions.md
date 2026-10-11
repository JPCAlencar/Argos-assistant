# Sessões Windows e desbloqueio remoto

**Desbloqueio remoto autorizado continua sendo requisito explícito do ARGOS.**
A v0.1 detecta GUI e reserva contratos; não armazena senha Windows nem implementa desbloqueio.

## Detecção atual

OpenInputDesktop com leitura, nome do desktop, foreground e SM_REMOTESESSION.
Não usa SwitchDesktop ou escrita no desktop seguro.

| Estado | Interpretação |
|---|---|
| INTERACTIVE | Default com janela foreground |
| REMOTE_INTERACTIVE | Mesmo critério, com sinal de sessão remota |
| LOCKED_OR_SECURE | Acesso negado: possível bloqueio/login/UAC/Session 0 |
| SECURE_OR_UNKNOWN | Desktop não identificado ou sem evidência de GUI |
| UNSUPPORTED | Sistema não Windows |

Não distingue com certeza login/bloqueio/UAC. UAC pode bloquear temporariamente.
Dispositivo local informa ligado/conectado porque o processo está executando;
agente remoto desconectado não provaria que o computador está desligado.
Cada ferramenta declara GUI/background. Arquivos/processos podem funcionar bloqueados;
teclado/mouse/UIA/captura exigem sessão utilizável.
Core como serviço não concede GUI: Desktop Agent deve rodar na sessão do usuário.

[OpenInputDesktop](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openinputdesktop)
e [segurança de desktops](https://learn.microsoft.com/en-us/windows/win32/winstation/desktop-security-and-access-rights)
fundamentam a detecção. Ler um desktop não equivale a autenticar um usuário.

## Windows 11 Home e RustDesk

Windows Home não hospeda o Remote Desktop nativo, embora possa ser cliente.
Não pressupor RDP host neste equipamento.
[Edições suportadas pela Microsoft](https://learn.microsoft.com/en-us/windows-server/remote/remote-desktop-services/remotepc/remote-desktop-allow-access).

RDP em outra edição pode criar/redirecionar sessão diferente da física; conexão/desconexão pode
alterar resolução e disponibilidade de UIA. Verificar sessão real em cada tarefa.

Investigar RustDesk instalado como serviço no computador pessoal, com autenticação forte,
servidor confiável e controle de acesso. Serviço/elevação podem ser necessários antes do login
ou em telas elevadas. Isso exige validação específica de versão Windows, login/UAC e políticas;
instalação não garante compatibilidade universal.
[Documentação RustDesk Windows](https://rustdesk.com/docs/en/client/windows/).
Não instalar RustDesk, alterar autenticação nem distribuir ferramentas em computadores da empresa nesta etapa.

Fluxo remoto assistido a validar:

1. Core recebe GUI indisponível e informa necessidade de autenticação.
2. Proprietário estabelece conexão remota autenticada e entra/desbloqueia pelo mecanismo suportado.
3. Desktop Agent confirma sessão utilizável.
4. Tarefa/permissões são revalidadas antes de executar.
5. Sem GUI, executar apenas a parte autorizada compatível com background.

Usar rede privada autenticada/TLS; não abrir portas administrativas diretamente na internet.

## Credential Providers e autenticação automatizada autorizada

Credential Providers integram autenticação oficial, não autorizam automação comum a digitar
senha no Secure Desktop. Envolvem código privilegiado, assinatura, identidade, recuperação e segurança.
DPAPI protege armazenamento, mas não resolve login, MFA/Hello, políticas, bloqueio ou desktop seguro.

Antes de automatizar:

- Verificar mecanismo oficial, consentimento do proprietário, MFA/Hello e compatibilidade.
- Separar credenciais do Core/LLM e canal de tarefas; nunca oferecê-las em ferramenta genérica.
- Respeitar necessidade de confirmação/presença do usuário.
- Testar boot frio, suspensão, login, bloqueio, UAC, reconexão e desktop físico/remoto.
- Prever revogação, auditoria, falha fechada e recuperação.

Sem estratégia segura viável, manter autenticação remota assistida. O requisito permanece no roadmap.
Não utilizar AutoAdminLogon, bypass, desativação de proteções ou desbloqueio experimental.

RemoteSessionAdapter reserva status e request_assisted_authentication. Futuro contrato deverá
expressar energia, conectividade, LOGIN/LOCKED/INTERACTIVE/REMOTE/UNKNOWN, método suportado
e ação do usuário necessária. Wake-on-LAN pode acordar equipamento compatível, mas não autentica.
Essas operações não são ferramentas disponíveis na v0.1.
