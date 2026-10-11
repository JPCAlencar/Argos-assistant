'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = value => esc(JSON.stringify(value, null, 2));
const money = value => '$ ' + Number(value || 0).toFixed(5);
const date = value => new Date(value).toLocaleString('pt-BR');
const paths = {
  grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  chat:'M21 11a8 8 0 0 1-8 8H7l-4 3V7a4 4 0 0 1 4-4h6a8 8 0 0 1 8 8z M7 8h9 M7 12h6',
  flow:'M3 3h6v6H3z M15 15h6v6h-6z M6 9v9h9 M15 3h6v6h-6z M9 6h6',
  clock:'M12 8v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
  spark:'M12 3l3 6 6 3-6 3-3 6-3-6-6-3 6-3z',
  tool:'M15 4a6 6 0 0 0-7 8L3 17l4 4 5-5a6 6 0 0 0 8-7l-4 3-4-4z',
  monitor:'M3 4h18v13H3z M8 21h8 M12 17v4',
  layers:'M12 3l10 6-10 6L2 9z M2 13l10 6 10-6 M2 17l10 6 10-6',
  shield:'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
  settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2'
};
const icon = name => '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + paths[name] + '"/></svg>';
document.querySelectorAll('[data-icon]').forEach(node => node.insertAdjacentHTML('afterbegin', icon(node.dataset.icon)));
const labels = {dashboard:'Dashboard',chat:'Conversar',workflows:'Automações',tasks:'Histórico',provider:'Modelos',tools:'Ferramentas',devices:'Dispositivos',memory:'Memória',security:'Segurança',settings:'Configurações'};
const state = {csrf:'',page:'',conversation:null,task:null,stream:null,timer:null,offset:0,render:0};
const statusLabels = {PENDING:'Na fila',PLANNING:'Planejando',RUNNING:'Em execução',WAITING_APPROVAL:'Aguardando aprovação',COMPLETED:'Concluída',FAILED:'Falhou',CANCELLED:'Cancelada',TIMED_OUT:'Tempo esgotado',APPROVED:'Aprovada',CONSUMED:'Autorizada e utilizada',DENIED:'Negada',EXPIRED:'Expirada',INTERRUPTED:'Interrompida'};
const terminal = status => ['COMPLETED','FAILED','CANCELLED','TIMED_OUT'].includes(status);
function badge(status) {
  const color = ['COMPLETED','ONLINE','INTERACTIVE','REMOTE_INTERACTIVE'].includes(status) ? 'good' :
    ['FAILED','TIMED_OUT','DENIED','LOCKED_OR_SECURE'].includes(status) ? 'bad' : 'warn';
  return '<span class="badge ' + color + '">' + esc(statusLabels[status] || status) + '</span>';
}
function toast(message, failure = false) {
  const node = $('#toast'); node.textContent = message; node.classList.toggle('failure', failure); node.hidden = false;
  clearTimeout(toast.timeout); toast.timeout = setTimeout(() => node.hidden = true, 6000);
}
async function api(path, method = 'GET', body) {
  const response = await fetch('/api' + path, {method, credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},
    body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== '/login') { showLogin(); throw new Error(data.detail || 'Entre novamente.'); }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(e => e.loc.join('.') + ': ' + e.msg).join('; ') : data.detail;
    throw new Error(detail || 'Não foi possível concluir a operação.');
  }
  return data;
}
function showLogin() {
  state.stream?.close(); clearInterval(state.timer); $('#shell').hidden = true; $('#login-view').hidden = false;
}
function heading(title, subtitle, action = '') {
  return '<div class="page-heading"><div><p class="eyebrow">ARGOS CONTROL</p><h1>' + title + '</h1><p>' + subtitle + '</p></div>' + action + '</div>';
}
function bind(selector, event, handler, root = document) {
  root.querySelectorAll(selector).forEach(node => node.addEventListener(event, async e => {
    try { await handler(e, node); } catch (error) { toast(error.message, true); }
  }));
}
async function busy(button, action) {
  button.disabled = true;
  try { return await action(); } finally { button.disabled = false; }
}
function taskRows(tasks) {
  if (!tasks.length) return '<div class="empty">Ainda não há tarefas.<br>Converse com o ARGOS ou execute uma rotina.</div>';
  return tasks.map(t => '<div class="row"><div class="row-main"><div class="row-title">' + esc(t.objective) +
    '</div><div class="row-sub">' + date(t.created_at) + ' · ' + esc(t.model) + '</div></div><div class="row-actions">' +
    badge(t.status) + '<button data-detail="' + t.id + '">Ver</button>' +
    (!terminal(t.status) ? '<button class="danger" data-cancel="' + t.id + '">Cancelar</button>' : '') + '</div></div>').join('');
}
function taskBindings() {
  bind('[data-detail]', 'click', (_, node) => showTask(node.dataset.detail));
  bind('[data-cancel]', 'click', async (_, node) => {
    await busy(node, () => api('/tasks/' + node.dataset.cancel + '/cancel', 'POST'));
    toast('Tarefa cancelada.'); if (state.page !== 'chat') await navigate();
  });
}
async function dashboard() {
  const data = await api('/dashboard');
  return heading('Tudo sob seu controle.', 'Acompanhe seu assistente e o que está acontecendo no seu computador.',
    '<span class="badge good"><span class="status-dot"></span> Sistema online</span>') +
    '<section class="hero"><div><p class="eyebrow">SEU ASSISTENTE PESSOAL</p><h2>Uma ideia. Um comando. Vamos executar.</h2><p>Converse naturalmente. Acompanhe as ações. Você decide as permissões.</p><div class="spaced"><a class="primary" href="#chat">Conversar com ARGOS →</a></div></div><img class="hero-emblem" src="/static/assets/argos.svg" alt=""></section>' +
    '<section class="stats"><div class="card"><div class="stat-label">Tarefas em andamento' + icon('flow') + '</div><div class="stat-value">' + data.running +
    '</div><div class="stat-foot">Execução local, com limites</div></div><div class="card"><div class="stat-label">Modelo ativo' + icon('spark') + '</div><div class="stat-value small-model">' +
    esc(data.model) + '</div><div class="stat-foot">DeepSeek · ' + (data.provider_configured ? 'Chave configurada' : 'Configuração pendente') +
    '</div></div><div class="card"><div class="stat-label">Tokens utilizados' + icon('layers') + '</div><div class="stat-value">' +
    Number(data.tokens).toLocaleString('pt-BR') + '</div><div class="stat-foot">' + data.calls +
    ' chamadas registradas</div></div><div class="card"><div class="stat-label">Custo estimado' + icon('clock') +
    '</div><div class="stat-value">' + money(data.estimated_cost) + '</div><div class="stat-foot">USD · estimativa, não cobrança oficial</div></div></section>' +
    '<div class="grid-two"><section class="card"><div class="card-heading"><h2>Últimas tarefas</h2><a href="#tasks">Ver histórico ↗</a></div>' +
    taskRows(data.tasks) + '</section><section class="card"><div class="card-heading"><h2>Computador local</h2>' + badge('ONLINE') +
    '</div><div class="device-head"><div class="device-icon">' + icon('monitor') + '</div><div><strong>' +
    esc(data.device.name) + '</strong><small>' + esc(data.device.os.split('-').slice(0,2).join(' ')) + '</small></div></div><div class="kv"><span>Sessão gráfica</span><strong>' +
    (data.device.session.interactive ? 'Disponível' : 'Indisponível') + '</strong></div><div class="kv"><span>Conexão</span><strong>Agente local conectado</strong></div><div class="quick-actions"><button data-go="workflows">Executar uma rotina <span>→</span></button><button data-go="provider">Configurar modelo <span>→</span></button><button data-go="security">Revisar permissões <span>→</span></button></div></section></div>' +
    (data.alerts.length ? '<h2 class="subheading">Atenção ao ambiente</h2>' + data.alerts.map(a => '<div class="alert">' + esc(a) + '</div>').join('') : '');
}
async function historyPage() {
  const data = await api('/tasks?limit=30&offset=' + state.offset);
  return heading('Histórico de tarefas', 'Objetivos, ações observadas e resultados persistidos.') +
    '<section class="card">' + taskRows(data) + pager(data.length) + '</section>';
}
function pager(count) {
  return '<div class="pagination"><button data-prev ' + (state.offset ? '' : 'disabled') + '>← Anteriores</button><button data-next ' +
    (count < 30 ? 'disabled' : '') + '>Próximos →</button></div>';
}
async function showTask(id) {
  const task = await api('/tasks/' + id);
  $('#detail-content').innerHTML = '<h3>' + esc(task.objective) + '</h3>' + badge(task.status) +
    '<p class="small muted">' + date(task.created_at) + ' · ' + esc(task.model) + ' · ' + money(task.estimated_cost) + ' estimados</p>' +
    (task.result ? '<p class="full-text">' + esc(task.result) + '</p>' : '') +
    (task.error ? '<p class="error full-text">' + esc(task.error) + '</p>' : '') +
    '<h3 class="detail-section">Ações registradas</h3>' +
    (task.executions.length ? task.executions.map(e => '<div class="detail-section"><div class="horizontal"><strong class="tool-name">' +
    esc(e.tool) + '</strong>' + badge(e.status) + '</div><pre>' + pretty({arguments:e.arguments,result:e.result,duration_ms:e.duration_ms}) + '</pre>' +
    (e.result?.asset ? '<a href="/api/screenshots/' + encodeURIComponent(e.result.asset) + '" target="_blank" rel="noopener">Ver captura autorizada ↗</a>' : '') + '</div>').join('') : '<p class="muted small">Nenhuma ferramenta executada.</p>') +
    '<h3 class="detail-section">Consumo do modelo</h3><pre>' + pretty(task.usage) + '</pre>';
  $('#detail-dialog').showModal();
}
async function providerPage() {
  const c = await api('/provider');
  return heading('Inteligência, configurada por você.', 'Credenciais protegidas e capacidades explícitas para o seu modelo.') +
    '<div class="grid-two"><section class="card"><div class="card-heading"><h2>Conexão DeepSeek</h2>' +
    '<span class="badge ' + (c.key_configured ? 'good' : 'warn') + '">' + (c.key_configured ? 'Chave salva' : 'Chave pendente') + '</span></div>' +
    '<form id="provider-form"><div class="form-grid">' + textField('model','Modelo',c.model) +
    textField('base_url','Base URL oficial',c.base_url) + '<label class="full">API key<input name="api_key" type="password" autocomplete="new-password" placeholder="' +
    (c.key_configured ? 'Deixe vazio para manter a chave atual' : 'Cole sua chave aqui') + '"></label>' +
    numberField('timeout','Timeout (segundos)',c.timeout,5,180) + numberField('max_tokens','Máximo de tokens de saída',c.max_tokens,128,8192) +
    numberField('input_price','USD / 1M tokens de entrada',c.input_price,0,1000,'0.000001') +
    numberField('cached_input_price','USD / 1M tokens em cache',c.cached_input_price,0,1000,'0.000001') +
    numberField('output_price','USD / 1M tokens de saída',c.output_price,0,1000,'0.000001') +
    '<div class="full checkbox-list">' + checkField('thinking','Modo de raciocínio',c.thinking) +
    checkField('vision','Entrada de imagens',c.vision) + checkField('tool_calling','Tool calling',c.tool_calling) +
    checkField('structured_output','Capacidade de JSON output',c.structured_output) +
    checkField('clear_key','Remover a chave salva',false) + '</div></div><div class="form-actions"><button class="primary" type="submit">Salvar configuração</button></div></form></section>' +
    '<aside class="card"><h2>Uso transparente</h2><p class="section-note spaced">O teste abaixo faz uma chamada real à API e pode consumir saldo. Ele usa a configuração já salva.</p><button id="test-provider">Testar conexão real ↗</button><p id="provider-test-result" class="small"></p><h3 class="subheading">Estimativas de custo</h3><p class="section-note">Valores em USD por milhão de tokens. Os preços iniciais usam a tarifa de pico consultada em 11/10/2026. Ajuste os valores conforme seu modelo e horário. Cache é contabilizado quando informado. A estimativa não substitui a cobrança do provedor.</p><h3>Dados enviados</h3><p class="section-note">Contexto recente, preferências autorizadas e observações de ferramentas. Capturas precisam ser habilitadas e aprovadas individualmente.</p><a class="small" href="https://api-docs.deepseek.com/quick_start/pricing/" target="_blank" rel="noopener">Documentação e preços oficiais ↗</a></aside></div>';
}
function textField(name, label, value, className = '') {
  return '<label class="' + className + '">' + label + '<input name="' + name + '" value="' + esc(value) + '" required></label>';
}
function numberField(name, label, value, min, max, step = '1') {
  return '<label>' + label + '<input type="number" name="' + name + '" value="' + value + '" min="' + min + '" max="' + max + '" step="' + step + '" required></label>';
}
function checkField(name, label, value) {
  return '<label class="check"><input type="checkbox" name="' + name + '" ' + (value ? 'checked' : '') + '>' + label + '</label>';
}
function formValues(form, numeric = [], boolean = []) {
  const result = Object.fromEntries(new FormData(form));
  numeric.forEach(key => result[key] = Number(result[key]));
  boolean.forEach(key => result[key] = form.elements[key].checked);
  return result;
}
async function toolsPage() {
  const tools = await api('/tools');
  return heading('Ferramentas do agente', 'Capacidades reais, com schemas, riscos e permissões visíveis.') +
    '<section class="card"><p class="section-note">Desativar uma ferramenta bloqueia novas execuções, inclusive chamadas aguardando aprovação. Operações em andamento precisam ser canceladas pela tarefa.</p><div class="table-wrap"><table><thead><tr><th>Ferramenta</th><th>Risco / permissão</th><th>Disponibilidade</th><th>Ativa</th></tr></thead><tbody>' +
    tools.map(t => '<tr><td><strong class="tool-name">' + esc(t.name) + '</strong><p>' + esc(t.description) +
    '</p><details><summary class="small muted">Schema de parâmetros</summary><pre>' + pretty(t.schema) + '</pre></details></td><td>' +
    '<span class="badge ' + (t.risk === 'HIGH' ? 'bad' : t.risk === 'MODERATE' ? 'warn' : 'good') + '">' + esc(t.risk) +
    '</span><p>' + esc(t.permission) + '<br>' + t.timeout + 's · ' + (t.gui ? 'Desktop interativo' : 'Background') + '</p></td><td>' +
    (t.available ? '<span class="badge good">Local</span>' : '<span class="badge">Indisponível neste SO</span>') +
    '</td><td><input class="switch" aria-label="Ativar ' + esc(t.name) + '" data-tool="' + esc(t.name) +
    '" type="checkbox" ' + (t.enabled ? 'checked' : '') + '></td></tr>').join('') + '</tbody></table></div></section>';
}
async function workflowsPage() {
  const [workflows,runs] = await Promise.all([api('/workflows'),api('/workflow-runs')]);
  return heading('Rotinas que poupam tempo.', 'Etapas determinísticas, sem uma chamada de IA a cada ação.') +
    '<div class="workflow-grid">' + workflows.map(w => '<section class="card workflow-card"><div class="card-heading"><h2>' +
    esc(w.name) + '</h2><span class="badge">v' + w.definition.version + '</span></div><p>' + esc(w.description) + '</p>' +
    w.definition.steps.map((s,i) => '<div class="step"><span>' + String(i+1).padStart(2,'0') + '</span>' + esc(s.tool) + '</div>').join('') +
    '<details><summary class="small muted">Definição</summary><pre>' + pretty(w.definition) + '</pre></details><label>Parâmetros JSON<textarea data-params="' +
    w.id + '">{}</textarea></label><button class="primary" data-workflow="' + w.id + '">Executar rotina →</button></section>').join('') + '</div>' +
    '<section class="card spaced"><h2>Cadastrar rotina</h2><p class="section-note spaced">Use apenas ferramentas existentes. Etapas obedecem às mesmas aprovações e limites do agente. Retry só é permitido para leituras.</p><form id="workflow-form"><div class="form-grid">' +
    textField('name','Nome','') + textField('description','Descrição','') +
    '<label class="full">Definição JSON<textarea name="definition" rows="9" required>' + pretty({version:1,timeout:120,risk_policy:'per_step',recovery:'stop',
      parameters:{type:'object',properties:{},additionalProperties:false},steps:[{tool:'get_system_status',arguments:{},retries:0,timeout:20,success:{confirmed:true}}]}) +
    '</textarea></label></div><button class="primary">Cadastrar rotina</button></form></section><section class="card spaced"><h2>Execuções recentes</h2>' +
    (runs.length ? runs.map(r => '<div class="row"><div class="row-main"><div class="row-title">Execução ' + esc(r.id.slice(0,8)) +
    '</div><div class="row-sub">' + date(r.created_at) + (r.failed_step !== null ? ' · Falha na etapa ' + (r.failed_step+1) : '') +
    '</div></div><div class="row-actions">' + badge(r.status) + '<button data-detail="' + r.task_id + '">Ver ações</button></div></div>').join('') :
    '<div class="empty">Nenhuma rotina executada ainda.</div>') + '</section>';
}
async function devicesPage() {
  const devices = await api('/devices');
  return heading('Seu computador, conectado.', 'Estado do agente e disponibilidade da sessão gráfica.') +
    devices.map(d => '<section class="card"><div class="card-heading"><h2>' + esc(d.name) + '</h2>' + badge('ONLINE') +
    '</div><div class="kv"><span>Sistema</span><strong>' + esc(d.os) + '</strong></div><div class="kv"><span>Último contato</span><strong>' +
    date(d.last_seen) + '</strong></div><div class="kv"><span>Energia</span><strong>Ligado</strong></div><div class="kv"><span>Sessão</span><strong>' +
    esc(d.session.state) + '</strong></div><p class="section-note spaced">' + esc(d.session.message) +
    '</p><h3>Ferramentas locais disponíveis</h3><pre>' + pretty(d.available_tools) + '</pre></section>').join('') +
    '<section class="card spaced"><h2>Acesso remoto · planejado para v0.3</h2><p class="section-note spaced">A versão atual identifica disponibilidade do desktop. Desbloqueio remoto assistido, Wake-on-LAN e agentes autenticados na rede são evoluções previstas. O ARGOS não armazena a senha do Windows.</p></section>';
}
async function memoryPage() {
  const memories = await api('/memories?limit=30&offset=' + state.offset);
  return heading('O que o ARGOS pode lembrar.', 'Preferências salvas por você. Edite ou remova quando quiser.') +
    '<section class="card"><h2>Nova preferência</h2><form id="memory-form" class="spaced"><div class="form-grid">' +
    textField('key','Nome da preferência','') + textField('value','Valor','') +
    '</div><button class="primary">Salvar preferência autorizada</button></form><p class="small muted">Não use este espaço para senhas ou credenciais.</p></section>' +
    '<section class="card spaced"><h2>Preferências salvas</h2>' +
    (memories.length ? memories.map(m => '<form class="memory-edit row" data-id="' + m.id + '"><div class="row-main">' +
    '<label>Preferência<input name="key" value="' + esc(m.key) + '" required></label><label>Valor<textarea name="value" required>' + esc(m.value) +
    '</textarea></label></div><div class="row-actions"><button type="submit">Salvar</button><button type="button" class="danger" data-delete-memory="' +
    m.id + '">Remover</button></div></form>').join('') : '<div class="empty">O ARGOS ainda não tem preferências salvas.</div>') +
    pager(memories.length) + '</section>';
}
function approvalCard(a) {
  return '<div class="approval-card"><h3>' + esc(a.tool) + ' · ' + esc(a.risk) +
    '</h3><p class="small muted">A aprovação autoriza somente esta ação com estes parâmetros. Capturas podem ser enviadas ao DeepSeek.</p><pre>' +
    pretty(a.arguments) + '</pre><button class="primary" data-approve="' + a.id + '">Aprovar esta ação</button><button data-deny="' + a.id + '">Negar</button></div>';
}
function approvalBindings() {
  for (const [selector,approved] of [['[data-approve]',true],['[data-deny]',false]]) {
    bind(selector, 'click', async (_, node) => {
      const id = approved ? node.dataset.approve : node.dataset.deny;
      await busy(node, () => api('/approvals/' + id, 'POST', {approved}));
      node.closest('.approval-card').innerHTML = '<p class="small">' + (approved ? 'Ação aprovada.' : 'Ação negada.') + '</p>';
      if (state.page === 'security') await navigate();
    });
  }
}
async function securityPage() {
  const [approvals,audits,settings] = await Promise.all([api('/approvals?limit=30&offset=' + state.offset),api('/audit?limit=30&offset=' + state.offset),api('/settings')]);
  return heading('Autonomia com limites claros.', 'Aprovações específicas, auditoria persistente e controle de sessão.') +
    '<div class="grid-two"><section class="card"><h2>Ações aguardando você</h2>' +
    (approvals.some(a => a.status === 'PENDING') ? approvals.filter(a => a.status === 'PENDING').map(approvalCard).join('') :
    '<div class="empty">Nenhuma ação pendente de aprovação.</div>') +
    '<h3 class="subheading">Decisões recentes</h3>' + approvals.filter(a => a.status !== 'PENDING').map(a =>
    '<div class="row"><span class="tool-name">' + esc(a.tool) + '</span>' + badge(a.status) + '</div>').join('') +
    '</section><aside class="card"><h2>Política atual</h2><p class="section-note spaced">Baixo risco: execução autorizada. Moderado: ' +
    (settings.runtime.auto_approve_moderate ? 'aprovação automática habilitada.' : 'aprovação específica.') +
    ' Alto risco: aprovação específica obrigatória.</p><h3>Diretórios autorizados</h3><pre>' + pretty(settings.allowed_roots) +
    '</pre><p class="small muted">O escopo de arquivos é definido na inicialização. <a href="#settings">Revisar limites →</a></p><h3 class="subheading">Sessões administrativas</h3><button id="revoke-sessions">Revogar outras sessões</button>' +
    '<h3 class="subheading">Alterar minha senha</h3><form id="password-form"><label>Senha atual<input name="current_password" type="password" autocomplete="current-password" required></label><label>Nova senha (mínimo 12 caracteres)<input name="new_password" type="password" autocomplete="new-password" minlength="12" maxlength="256" required></label><button>Alterar senha</button></form></aside></div>' +
    '<section class="card spaced"><h2>Auditoria</h2><div class="table-wrap"><table><thead><tr><th>Quando</th><th>Ação</th><th>Referências</th></tr></thead><tbody>' +
    audits.map(a => '<tr><td>' + date(a.created_at) + '</td><td>' + esc(a.action) + '</td><td><code>' +
    esc(JSON.stringify(a.data)) + '</code></td></tr>').join('') + '</tbody></table></div>' + pager(audits.length) + '</section>';
}
async function settingsPage() {
  const data = await api('/settings'), c = data.runtime;
  return heading('Ajustes do seu assistente', 'Configurações persistentes. Novos limites se aplicam às próximas tarefas.') +
    '<section class="card"><form id="settings-form"><div class="form-grid">' + textField('name','Nome do assistente',c.name) +
    '<label>Idioma<select name="language"><option value="pt-BR">Português brasileiro</option></select></label>' +
    textField('style','Estilo de comunicação',c.style,'full') +
    numberField('max_steps','Máximo de passos',c.max_steps,1,100) + numberField('max_calls','Máximo de chamadas IA',c.max_calls,1,50) +
    numberField('task_timeout','Timeout da tarefa (segundos)',c.task_timeout,5,3600) + numberField('tool_timeout','Timeout por ferramenta (segundos)',c.tool_timeout,1,120) +
    numberField('max_attempts','Tentativas máximas para leitura',c.max_attempts,1,3) + numberField('max_concurrent','Tarefas simultâneas',c.max_concurrent,1,4) +
    numberField('context_chars','Limite de contexto (caracteres)',c.context_chars,2000,100000) +
    numberField('max_task_cost','Orçamento estimado / tarefa (USD)',c.max_task_cost,0.001,100,'0.001') +
    numberField('screenshot_retention_hours','Retenção de capturas (horas)',c.screenshot_retention_hours,1,168) +
    numberField('event_retention_days','Retenção do feed de eventos (dias)',c.event_retention_days,1,365) +
    '<div class="full">' + checkField('auto_approve_moderate','Autorizar automaticamente ferramentas de risco moderado',c.auto_approve_moderate) +
    checkField('screenshots_enabled','Permitir capturas, com confirmação específica antes de capturar e enviar à IA',c.screenshots_enabled) +
    '</div></div><button class="primary">Salvar configurações</button></form></section>' +
    '<div class="card spaced"><h2>Escopo de arquivos</h2><pre>' + pretty(data.allowed_roots) +
    '</pre><p class="section-note">Para mudar o escopo, ajuste ARGOS_ALLOWED_ROOTS no .env e reinicie o servidor. Credenciais e arquivos internos permanecem protegidos.</p><h3>Voz · v0.2 planejada</h3><p class="small muted">Push-to-talk, transcrição e síntese locais terão adaptadores próprios.</p></div>';
}
function messageHtml(role, content) {
  return '<div class="message ' + esc(role) + '"><span class="avatar">' + (role === 'user' ? 'EU' : 'A') +
    '</span><div class="message-body"><small>' + (role === 'user' ? 'Você' : 'ARGOS') + '</small><p>' + esc(content) + '</p></div></div>';
}
async function chatPage() {
  const conversations = await api('/conversations');
  const messages = state.conversation ? await api('/conversations/' + state.conversation + '/messages') : [];
  return heading('Converse com ARGOS.', 'Diga o objetivo. Veja as ações. Aprove quando necessário.') +
    '<section class="chat-layout"><aside class="conversation-sidebar"><button id="new-conversation">＋ Nova conversa</button><p class="nav-caption">CONVERSAS RECENTES</p><div id="conversation-list">' +
    conversations.map(c => '<button class="conversation-item ' + (c.id === state.conversation ? 'selected' : '') +
    '" data-conversation="' + c.id + '">' + esc(c.title) + '</button>').join('') + '</div></aside><div class="chat-main"><div class="chat-messages" id="messages">' +
    (messages.length ? messages.map(m => messageHtml(m.role,m.content)).join('') :
    '<div class="chat-welcome"><img src="/static/assets/argos.svg" width="52" height="52" alt=""><h2>O que vamos fazer hoje?</h2><p>Posso consultar seu computador, trabalhar com arquivos autorizados e executar rotinas.</p><div class="suggestions"><button data-suggestion="Olá, Argos. O que você consegue fazer?">Conhecer o ARGOS</button><button data-suggestion="Consulte o status do sistema.">Consultar o computador</button><button data-suggestion="Abra o Bloco de Notas.">Abrir Bloco de Notas</button></div></div>') +
    '</div><div id="task-progress" class="task-progress" hidden><div class="task-progress-head"><span id="task-status"></span><button id="cancel-current" class="danger">Cancelar tarefa</button></div><div id="action-feed" class="action-feed"></div><div id="task-approvals"></div></div><div class="chat-composer"><form id="chat-form"><textarea id="objective" name="objective" placeholder="Peça algo ao ARGOS…" aria-label="Mensagem para ARGOS" maxlength="8000" required></textarea><button class="primary" id="send-message">Enviar ↑</button></form><span class="small muted">Enter para enviar · Shift + Enter para nova linha · Ações sensíveis precisam da sua aprovação.</span></div></div></section>';
}
function appendMessage(role, content) {
  $('#messages .chat-welcome')?.remove(); $('#messages').insertAdjacentHTML('beforeend',messageHtml(role,content));
  $('#messages').scrollTop = $('#messages').scrollHeight;
}
async function watchTask(task) {
  state.task = task.id; state.conversation = task.conversation_id || state.conversation;
  if (state.page !== 'chat') { window.history.replaceState(null, '', '#chat'); await navigate(); }
  $('#task-progress').hidden = false;
  $('#send-message').disabled = true; $('#task-status').innerHTML = badge(task.status);
  state.stream?.close();
  const stream = new EventSource('/api/tasks/' + task.id + '/events'); state.stream = stream;
  stream.addEventListener('update', e => {
    if (state.page !== 'chat') return;
    const event = JSON.parse(e.data), data = event.data;
    if (event.kind === 'status') {
      $('#task-status').innerHTML = badge(data.status);
      if (data.status === 'RUNNING') $('#task-approvals').innerHTML = '';
      if (terminal(data.status)) $('#cancel-current').hidden = true;
    }
    if (event.kind === 'tool_start' || event.kind === 'tool_end' || event.kind === 'model_call' || event.kind === 'workflow_step' || event.kind === 'retry') {
      const text = event.kind === 'model_call' ? 'Consultando ' + data.model + ' · chamada ' + data.call :
        event.kind === 'tool_start' ? 'Executando ' + data.tool :
        event.kind === 'tool_end' ? data.tool + ' · ' + (data.error || (data.confirmed ? 'resultado confirmado' : 'resultado sem confirmação')) :
        event.kind === 'retry' ? data.tool + ' · tentativa ' + data.attempt : data.workflow + ' · etapa ' + data.step;
      $('#action-feed').insertAdjacentHTML('beforeend','<div class="action-line">' + esc(text) + '</div>');
      $('#action-feed').scrollTop = $('#action-feed').scrollHeight;
    }
    if (event.kind === 'approval') {
      $('#task-approvals').insertAdjacentHTML('beforeend',approvalCard(data)); approvalBindings();
    }
  });
  stream.addEventListener('done', async () => {
    stream.close(); state.task = null;
    if (state.page !== 'chat') return;
    try {
      const final = await api('/tasks/' + task.id);
      appendMessage('assistant', final.result || final.error || 'Execução encerrada.');
      $('#send-message').disabled = false; $('#cancel-current').hidden = true;
      $('#task-approvals').innerHTML = ''; $('#task-status').innerHTML = badge(final.status);
    } catch (error) { toast(error.message,true); }
  });
  stream.onerror = () => { if (state.page === 'chat') $('#task-status').textContent = 'Reconectando ao servidor…'; };
}
function pageBindings() {
  taskBindings(); approvalBindings();
  bind('[data-go]', 'click', (_,n) => location.hash = n.dataset.go);
  bind('[data-prev]', 'click', async () => { state.offset = Math.max(0,state.offset-30); await navigate(); });
  bind('[data-next]', 'click', async () => { state.offset += 30; await navigate(); });
  bind('#provider-form', 'submit', async (e,n) => {
    e.preventDefault(); const values = formValues(n,['timeout','max_tokens','input_price','cached_input_price','output_price'],
      ['thinking','vision','tool_calling','structured_output','clear_key']); values.provider = 'deepseek';
    if (!values.api_key) delete values.api_key;
    await busy($('button[type=submit]',n), () => api('/provider','PUT',values)); toast('Configuração salva.'); await navigate();
  });
  bind('#test-provider','click', async (_,n) => {
    const result = await busy(n, () => api('/provider/test','POST'));
    $('#provider-test-result').textContent = result.response + ' · ' + money(result.estimated_cost) + ' estimados';
  });
  bind('[data-tool]','change', async (_,n) => {
    try { await api('/tools/' + n.dataset.tool,'PUT',{enabled:n.checked}); toast('Permissão atualizada.'); }
    catch (error) { n.checked = !n.checked; throw error; }
  });
  bind('#workflow-form','submit', async (e,n) => {
    e.preventDefault(); const values = formValues(n); values.definition = JSON.parse(values.definition);
    await busy($('button',n), () => api('/workflows','POST',values)); toast('Rotina cadastrada.'); await navigate();
  });
  bind('[data-workflow]','click', async (_,n) => {
    const field = document.querySelector('[data-params="' + n.dataset.workflow + '"]');
    const task = await busy(n, () => api('/workflows/' + n.dataset.workflow + '/run','POST',{parameters:JSON.parse(field.value)}));
    window.history.replaceState(null, '', '#chat'); await navigate(); await watchTask(task);
  });
  bind('#memory-form','submit', async (e,n) => { e.preventDefault(); await busy($('button',n), () => api('/memories','POST',formValues(n))); toast('Preferência salva.'); await navigate(); });
  bind('.memory-edit','submit', async (e,n) => { e.preventDefault(); await api('/memories/' + n.dataset.id,'PUT',formValues(n)); toast('Preferência atualizada.'); });
  bind('[data-delete-memory]','click', async (_,n) => { await api('/memories/' + n.dataset.deleteMemory,'DELETE'); toast('Preferência removida.'); await navigate(); });
  bind('#revoke-sessions','click', async (_,n) => { const r = await busy(n, () => api('/sessions/revoke','POST')); toast(r.count + ' sessões revogadas.'); });
  bind('#password-form','submit', async (e,n) => { e.preventDefault(); await busy($('button',n), () => api('/password','PUT',formValues(n))); n.reset(); toast('Senha alterada. Outras sessões foram revogadas.'); });
  bind('#settings-form','submit', async (e,n) => {
    e.preventDefault(); const values = formValues(n,['max_steps','max_calls','task_timeout','tool_timeout','max_attempts','max_concurrent',
      'context_chars','max_task_cost','screenshot_retention_hours','event_retention_days'],['auto_approve_moderate','screenshots_enabled']);
    await busy($('button',n), () => api('/settings','PUT',values)); toast('Configurações salvas.');
  });
  bind('#new-conversation','click', async () => { state.conversation = null; await navigate(); });
  bind('[data-conversation]','click', async (_,n) => { state.conversation = n.dataset.conversation; await navigate(); await restoreTask(); });
  bind('[data-suggestion]','click', (_,n) => { $('#objective').value = n.dataset.suggestion; $('#objective').focus(); });
  bind('#chat-form','submit', async (e,n) => {
    e.preventDefault(); const objective = $('#objective').value.trim(); if (!objective || state.task) return;
    const task = await busy($('#send-message'), () => api('/tasks','POST',{objective,conversation_id:state.conversation}));
    appendMessage('user',objective); $('#objective').value = ''; $('#action-feed').innerHTML = ''; $('#task-approvals').innerHTML = '';
    $('#cancel-current').hidden = false; await watchTask(task);
  });
  $('#objective')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chat-form').requestSubmit(); }
  });
  bind('#cancel-current','click', async (_,n) => { if (state.task) await busy(n, () => api('/tasks/' + state.task + '/cancel','POST')); });
}
async function restoreTask() {
  if (state.page !== 'chat') return;
  const tasks = await api('/tasks?limit=30');
  const active = tasks.find(t => !terminal(t.status) && (!state.conversation || t.conversation_id === state.conversation));
  if (active) {
    if (active.conversation_id && state.conversation !== active.conversation_id) {
      state.conversation = active.conversation_id; await navigate();
    }
    await watchTask(active);
  }
}
const renderers = {dashboard,chat:chatPage,tasks:historyPage,provider:providerPage,tools:toolsPage,workflows:workflowsPage,
  devices:devicesPage,memory:memoryPage,security:securityPage,settings:settingsPage};
async function navigate() {
  clearInterval(state.timer);
  const page = location.hash.slice(1) || 'dashboard';
  if (!renderers[page]) { location.hash = 'dashboard'; return; }
  if (state.page !== page) { state.offset = 0; state.stream?.close(); clearInterval(state.timer); }
  state.page = page; state.render++; const version = state.render;
  $('#content').classList.add('loading');
  $('#page-name').textContent = labels[page];
  document.querySelectorAll('[data-page]').forEach(n => n.classList.toggle('active',n.dataset.page === page));
  $('#sidebar').classList.remove('open'); $('#menu-button').setAttribute('aria-expanded','false');
  try {
    const html = await renderers[page]();
    if (version !== state.render) return;
    $('#content').innerHTML = html; pageBindings();
    if (['dashboard', 'tasks'].includes(page)) state.timer = setInterval(navigate, 5000);
  } catch (error) {
    if (version === state.render) $('#content').innerHTML = '<div class="alert">' + esc(error.message) + '</div>';
  } finally {
    if (version === state.render) $('#content').classList.remove('loading');
  }
}
$('#login-form').addEventListener('submit', async e => {
  e.preventDefault(); $('#login-error').textContent = '';
  try {
    const data = await busy($('#login-form button'), () => api('/login','POST',formValues(e.target)));
    state.csrf = data.csrf_token; $('#login-form input[name=password]').value = '';
    $('#login-view').hidden = true; $('#shell').hidden = false; await navigate(); await restoreTask();
  } catch (error) { $('#login-error').textContent = error.message; }
});
$('#logout').addEventListener('click', async () => { try { await api('/logout','POST'); showLogin(); } catch (e) { toast(e.message,true); } });
$('#menu-button').addEventListener('click', () => { $('#sidebar').classList.toggle('open'); $('#menu-button').setAttribute('aria-expanded',String($('#sidebar').classList.contains('open'))); });
$('#close-dialog').addEventListener('click', () => $('#detail-dialog').close());
window.addEventListener('hashchange', async () => { if (!$('#shell').hidden) { await navigate(); await restoreTask(); } });
document.addEventListener('click', e => { if (innerWidth <= 760 && !$('#sidebar').contains(e.target) && !$('#menu-button').contains(e.target)) $('#sidebar').classList.remove('open'); });
(async () => {
  try {
    const user = await api('/me'); state.csrf = user.csrf_token; $('#shell').hidden = false;
    await navigate(); await restoreTask();
  } catch { showLogin(); }
})();
