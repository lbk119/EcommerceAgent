import { completeOnboarding as mockCompleteOnboarding, loadSession as mockLoadSession, loginWithMock, logoutMock, registerWithMock, saveSession as mockSaveSession } from './mockAuth'
import type { AuthSession, IntegrationStatus, LoginPayload, OnboardingPayload, RegisterPayload, ReportDetail, Shop, StrategyStatus, StructuredResult, User, WorkspaceData } from '../types'

const STORAGE_KEY = 'ecompilot_session'
const API_BASE = '/api/v1'
const ENABLE_MOCK_FALLBACK = import.meta.env.DEV && import.meta.env.VITE_ENABLE_MOCK_FALLBACK === 'true'

type RequestOptions = RequestInit & {
  session?: AuthSession | null
}

type AuthResponse = {
  accessToken?: string
  access_token?: string
  user: Partial<User> & Record<string, unknown>
  workspace?: WorkspaceData
}

export class JobTimeoutError extends Error {
  constructor(message = '任务仍在后台执行，可稍后刷新工作台查看结果') {
    super(message)
    this.name = 'JobTimeoutError'
  }
}

export type ImportPreview = {
  rows: Record<string, string>[]
  fields: { sourceField: string; targetField: string; confidence: number }[]
  quality: { score: number; errors: string[] }
}

export type AgentJob = {
  id: string
  jobId?: string
  agentId?: string
  jobType?: string
  title?: string
  status: 'pending' | 'running' | 'waiting_review' | 'completed' | 'failed' | 'cancelled' | string
  taskId?: string
  conversationId?: string
  resultReportId?: string
  errorMessage?: string
}

export type ImportJobResult = {
  id: string
  status: string
  rows?: number
  fileName?: string
  workspaceShouldRefresh?: boolean
  generatedReportId?: string
  generatedStrategiesCount?: number
}

export type ImportRefreshResult = {
  session: AuthSession
  job: ImportJobResult
}

export type AiChatMessage = {
  id?: string
  role: 'user' | 'assistant'
  content: string
  source?: 'agent' | 'workflow' | 'deep_agent' | 'agent_timeout' | 'error' | string
  status?: 'queued' | 'running' | 'completed' | 'failed' | 'timeout' | 'cancelled' | string
  taskId?: string
  conversationId?: string
  intent?: string
  errorMessage?: string
  structuredResult?: StructuredResult | null
}

export type AiChatConversation = {
  id: string
  title: string
  status: string
  createdAt?: string
  updatedAt?: string
}

export type AiChatAccepted = {
  messageId: string
  conversationId: string
  taskId: string
  status: string
  source: string
  wsThreadId: string
  intent?: string
  acceptedLatencyMs?: number
  message: AiChatMessage
}

type RawAiChatMessage = Partial<AiChatMessage> & Record<string, unknown>
type RawAiChatAccepted = Partial<AiChatAccepted> & Record<string, unknown>

export type AgentProgressEvent = {
  type: 'agent_progress' | 'assistant_delta' | 'assistant_final' | 'agent_error' | 'monitor_event' | 'pong' | string
  event?: string
  eventType?: string
  message?: string
  title?: string
  detail?: string
  timestamp?: string
  taskId?: string
  messageId?: string
  conversationId?: string
  stage?: string
  status?: string
  latencyMs?: number
  delta?: string
  content?: string
  source?: string
  totalLatencyMs?: number
  errorMessage?: string
  recoverable?: boolean
  display?: { group?: string; importance?: 'high' | 'normal' | 'low' | string; collapsible?: boolean }
  data?: Record<string, unknown>
}

export type AiChatTimelineEvent = {
  timestamp?: string
  event_type?: string
  agent_name?: string
  workflow_name?: string
  step_name?: string
  latency_ms?: number
  error?: string
}

export type AgentRuntimeHealth = {
  agentRuntime: string
  taskQueue: Record<string, unknown>
  taskRuntime?: Record<string, unknown>
  monitor: Record<string, unknown>
  tracer: Record<string, unknown>
  memory: Record<string, unknown>
  evolution: Record<string, unknown>
}

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

export const loadSession = (): AuthSession | null => {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthSession
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

export const saveSession = (session: AuthSession) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
}

export const logout = () => {
  localStorage.removeItem(STORAGE_KEY)
  logoutMock()
}

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const bodyIsForm = options.body instanceof FormData
  if (!bodyIsForm && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (options.session?.token) headers.set('Authorization', `Bearer ${options.session.token}`)
  if (options.session?.workspace.currentShopId) headers.set('X-Shop-ID', options.session.workspace.currentShopId)

  const hasAuthHeader = headers.has('Authorization')
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  const text = await response.text()
  const data = parseResponseBody(text)
  if (response.status === 401) {
    if (options.session?.token || hasAuthHeader) {
      logout()
      window.dispatchEvent(new CustomEvent('ecompilot:auth-expired'))
      throw new Error('登录已过期，请重新登录')
    }
    throw new Error(extractErrorMessage(data, text) || '账号或密码错误')
  }
  if (!response.ok) {
    throw new Error(extractErrorMessage(data, text))
  }
  return data as T
}

function parseResponseBody(text: string): unknown {
  if (!text) return {}
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function extractErrorMessage(data: unknown, fallbackText: string): string {
  if (typeof data === 'string') return data || '请求失败'
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>
    const nested = record.error
    if (typeof nested === 'string') return nested
    if (nested && typeof nested === 'object') {
      const error = nested as Record<string, unknown>
      return String(error.message || error.details || error.code || '请求失败')
    }
    return String(record.detail || record.message || fallbackText || '请求失败')
  }
  return fallbackText || '请求失败'
}

async function withMockFallback<T>(factory: () => Promise<T>, fallback: () => Promise<T>): Promise<T> {
  try {
    return await factory()
  } catch (error) {
    if (!ENABLE_MOCK_FALLBACK) throw error
    console.warn('[EcomPilot] API 请求失败，启用开发 mock fallback。', error)
    return fallback()
  }
}

function normalizeUser(raw: AuthResponse['user']): User {
  return {
    id: String(raw.id || ''),
    name: String(raw.name || '运营负责人'),
    email: String(raw.email || raw.id || ''),
    phone: raw.phone ? String(raw.phone) : undefined,
    companyName: String(raw.companyName || raw.company_name || '默认组织'),
    role: String(raw.role || 'admin'),
    createdAt: String(raw.createdAt || raw.created_at || new Date().toISOString()),
    onboardingCompleted: Boolean(raw.onboardingCompleted ?? raw.onboarding_completed),
    plan: String(raw.plan || '团队版'),
    tenantIds: Array.isArray(raw.tenantIds) ? raw.tenantIds.map(String) : Array.isArray(raw.tenant_ids) ? raw.tenant_ids.map(String) : [],
    shopIds: Array.isArray(raw.shopIds) ? raw.shopIds.map(String) : Array.isArray(raw.shop_ids) ? raw.shop_ids.map(String) : [],
    defaultTenantId: String(raw.defaultTenantId || raw.default_tenant_id || ''),
    defaultShopId: String(raw.defaultShopId || raw.default_shop_id || '')
  }
}

async function sessionFromAuth(response: AuthResponse): Promise<AuthSession> {
  const token = response.accessToken || response.access_token || ''
  const user = normalizeUser(response.user)
  const workspace = response.workspace || emptyWorkspace()
  const session = { token, user, workspace }
  saveSession(session)
  return session
}

function emptyWorkspace(): WorkspaceData {
  return { currentShopId: '', shops: [], integrations: [], metrics: { date: '', gmv: 0, orders: 0, conversionRate: 0, averageOrderValue: 0, refundRate: 0, visitors: 0, inventoryRiskSkuCount: 0, activeCampaignProducts: 0, aiCompletedTasks: 0 }, products: [], agents: [], reports: [], strategies: [], campaigns: [], imports: [] }
}

export async function fetchWorkspace(session: AuthSession): Promise<WorkspaceData> {
  const data = await requestJson<{ workspace: WorkspaceData }>('/workspace', { method: 'GET', session })
  return data.workspace
}

export async function login(payload: LoginPayload): Promise<AuthSession> {
  return withMockFallback(
    // 登录只做认证并保存 token/user，工作区聚合由 App 在跳转后后台刷新，避免登录按钮被慢查询卡住。
    () => requestJson<AuthResponse>('/auth/login', { method: 'POST', body: JSON.stringify(payload) }).then(sessionFromAuth),
    () => loginWithMock(payload)
  )
}

export async function register(payload: RegisterPayload): Promise<AuthSession> {
  return withMockFallback(
    () => requestJson<AuthResponse>('/auth/register', { method: 'POST', body: JSON.stringify(payload) }).then(sessionFromAuth),
    () => registerWithMock(payload)
  )
}

export async function completeOnboarding(session: AuthSession, payload: OnboardingPayload): Promise<AuthSession> {
  return withMockFallback(async () => {
    const data = await requestJson<{ workspace: WorkspaceData }>('/onboarding/complete', { method: 'POST', body: JSON.stringify(payload), session })
    let user = { ...session.user, onboardingCompleted: true }
    let token = session.token
    try {
      const account = await requestJson<AuthResponse>('/auth/shops', { method: 'POST', body: JSON.stringify({ shop_id: data.workspace.currentShopId, shop_name: data.workspace.shops[0]?.name }), session })
      user = normalizeUser(account.user)
      token = account.accessToken || account.access_token || token
      const accountSession = { ...session, token, user, workspace: data.workspace }
      const completed = await requestJson<{ user: Partial<User> & Record<string, unknown> }>('/account/onboarding-completed', { method: 'POST', body: JSON.stringify({}), session: accountSession })
      user = normalizeUser(completed.user)
    } catch {
      // Brain 的引导已经完成；账号店铺绑定失败时保留前端会话态，避免阻断首次进入工作台。
      user = { ...user, onboardingCompleted: true }
    }
    const nextSession = { ...session, token, user, workspace: data.workspace }
    saveSession(nextSession)
    return nextSession
  }, () => mockCompleteOnboarding(session, payload))
}

export async function refreshWorkspace(session: AuthSession): Promise<AuthSession> {
  const workspace = await fetchWorkspace(session)
  const nextSession = { ...session, workspace }
  saveSession(nextSession)
  return nextSession
}

export async function createOrUpdateShop(session: AuthSession, shop: Partial<Shop>, shopId?: string): Promise<AuthSession> {
  const method = shopId ? 'PUT' : 'POST'
  const path = shopId ? `/shops/${shopId}` : '/shops'
  const nextShop = shopId ? shop : { ...shop, id: crypto.randomUUID() }
  if (!shopId) {
    await requestJson('/auth/shops', { method: 'POST', body: JSON.stringify({ shop_id: nextShop.id, shop_name: nextShop.name }), session })
  }
  await requestJson(path, { method, body: JSON.stringify(nextShop), session })
  return refreshWorkspace(session)
}

export async function removeShop(session: AuthSession, shopId: string): Promise<AuthSession> {
  await requestJson(`/shops/${shopId}`, { method: 'DELETE', session })
  const shops = session.workspace.shops.filter((shop) => shop.id !== shopId)
  const currentShopId = session.workspace.currentShopId === shopId ? shops[0]?.id || '' : session.workspace.currentShopId
  const nextSession = { ...session, workspace: { ...session.workspace, shops, currentShopId } }
  saveSession(nextSession)
  return nextSession
}

export async function setIntegration(session: AuthSession, platform: string, status: IntegrationStatus): Promise<AuthSession> {
  await requestJson('/integrations/status', { method: 'POST', body: JSON.stringify({ platform, status }), session })
  return refreshWorkspace(session)
}

export async function setStrategyStatus(session: AuthSession, strategyId: string, status: StrategyStatus): Promise<AuthSession> {
  const actionByStatus: Record<StrategyStatus, string> = { accepted: 'approve', rejected: 'reject', deferred: 'defer', pending: 'defer' }
  await requestJson(`/agents/strategies/${strategyId}/${actionByStatus[status]}`, { method: 'POST', session })
  return session
}

export async function startAgentJob(session: AuthSession, agentId: string, title: string): Promise<AgentJob> {
  const jobTypeByAgent: Record<string, string> = { 'store-analyst': 'daily_report', 'product-assistant': 'product_optimization', 'inventory-inspector': 'inventory_risk_scan', 'campaign-reviewer': 'campaign_review', 'report-specialist': 'weekly_report' }
  const data = await requestJson<{ job: AgentJob }>(`/agents/${agentId}/jobs`, { method: 'POST', body: JSON.stringify({ jobType: jobTypeByAgent[agentId] || 'daily_report', title, params: {} }), session })
  return data.job
}

export async function importSampleData(session: AuthSession): Promise<ImportRefreshResult> {
  const data = await requestJson<{ job: ImportJobResult }>('/data-import/sample', { method: 'POST', session })
  return { session: await refreshWorkspace(session), job: data.job }
}

export async function uploadImportFile(session: AuthSession, file: File): Promise<{ id: string; fileName: string; status: string }> {
  const body = new FormData()
  body.append('file', file)
  const data = await requestJson<{ job: { id: string; fileName: string; status: string } }>('/data-import/upload', { method: 'POST', body, session })
  return data.job
}

export async function pasteImportText(session: AuthSession, text: string): Promise<{ id: string; fileName: string; status: string }> {
  const data = await requestJson<{ job: { id: string; fileName: string; status: string } }>('/data-import/paste', { method: 'POST', body: JSON.stringify({ text }), session })
  return data.job
}

export async function previewImportJob(session: AuthSession, jobId: string): Promise<ImportPreview> {
  return requestJson<ImportPreview>(`/data-import/${jobId}/preview`, { method: 'GET', session })
}

export async function confirmImportJob(session: AuthSession, jobId: string): Promise<ImportRefreshResult> {
  const job = await requestJson<ImportJobResult>(`/data-import/${jobId}/confirm`, { method: 'POST', session })
  return { session: await refreshWorkspace(session), job }
}

export async function generateReport(session: AuthSession, type: string, title: string, agentId: string): Promise<{ reportId: string; job: AgentJob }> {
  return requestJson<{ reportId: string; job: AgentJob }>('/reports/generate', { method: 'POST', body: JSON.stringify({ type, title, agentId }), session })
}

export async function analyzeProducts(session: AuthSession): Promise<AgentJob> {
  const data = await requestJson<{ job: AgentJob }>('/products/analyze', { method: 'POST', body: JSON.stringify({ title: '商品优化分析', params: {} }), session })
  return data.job
}

export async function generateReplenishmentPlan(session: AuthSession): Promise<AgentJob> {
  const data = await requestJson<{ job: AgentJob }>('/inventory/replenishment-plan', { method: 'POST', body: JSON.stringify({ title: '库存补货建议', params: {} }), session })
  return data.job
}

export async function reviewCampaign(session: AuthSession, campaignId: string): Promise<AgentJob> {
  const data = await requestJson<{ job: AgentJob }>(`/campaigns/${campaignId}/review`, { method: 'POST', body: JSON.stringify({ title: '活动复盘报告', params: {} }), session })
  return data.job
}

export async function getAgentJob(session: AuthSession, jobId: string): Promise<AgentJob> {
  const data = await requestJson<{ job: AgentJob }>(`/agents/jobs/${jobId}`, { method: 'GET', session })
  return data.job
}

export async function waitForJobAndRefresh(session: AuthSession, jobId: string): Promise<AuthSession> {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const job = await getAgentJob(session, jobId)
    if (job.status === 'completed') return refreshWorkspace(session)
    if (['failed', 'cancelled'].includes(job.status)) throw new Error(job.errorMessage || `任务${job.status === 'cancelled' ? '已取消' : '执行失败'}`)
    await sleep(2000)
  }
  throw new JobTimeoutError()
}

export async function getReport(session: AuthSession, reportId: string): Promise<ReportDetail> {
  const data = await requestJson<{ report: ReportDetail }>(`/reports/${reportId}`, { method: 'GET', session })
  return data.report
}

export async function sendAiMessage(session: AuthSession, content: string, conversationId?: string): Promise<AiChatAccepted> {
  const data = await requestJson<RawAiChatAccepted>('/ai-chat/messages', { method: 'POST', body: JSON.stringify({ content, conversationId }), session })
  return normalizeAiChatAccepted(data)
}

export async function getAiChatMessage(session: AuthSession, messageId: string): Promise<AiChatMessage> {
  const data = await requestJson<{ message: RawAiChatMessage }>(`/ai-chat/messages/${messageId}`, { method: 'GET', session })
  return normalizeAiChatMessage(data.message)
}

export async function getAiChatTimeline(session: AuthSession, taskId: string): Promise<{ task_id: string; events: AiChatTimelineEvent[]; run?: Record<string, unknown> }> {
  return requestJson<{ task_id: string; events: AiChatTimelineEvent[]; run?: Record<string, unknown> }>(`/ai-chat/tasks/${taskId}/timeline`, { method: 'GET', session })
}

export async function listAiChatConversations(session: AuthSession): Promise<AiChatConversation[]> {
  const data = await requestJson<{ conversations: AiChatConversation[] }>('/ai-chat/conversations', { method: 'GET', session })
  return data.conversations
}

export async function fetchAiChatMessages(session: AuthSession, conversationId: string): Promise<AiChatMessage[]> {
  const data = await requestJson<{ messages: RawAiChatMessage[] }>(`/ai-chat/conversations/${conversationId}/messages`, { method: 'GET', session })
  return data.messages.map(normalizeAiChatMessage)
}

function normalizeAiChatAccepted(data: RawAiChatAccepted): AiChatAccepted {
  const message = normalizeAiChatMessage((data.message as RawAiChatMessage | undefined) || {})
  return {
    messageId: String(data.messageId || data.message_id || message.id || ''),
    conversationId: String(data.conversationId || data.conversation_id || message.conversationId || ''),
    taskId: String(data.taskId || data.task_id || message.taskId || ''),
    status: String(data.status || message.status || 'running'),
    source: String(data.source || message.source || 'agent'),
    wsThreadId: String(data.wsThreadId || data.ws_thread_id || data.conversationId || data.conversation_id || message.conversationId || ''),
    intent: typeof data.intent === 'string' ? data.intent : message.intent,
    acceptedLatencyMs: typeof data.acceptedLatencyMs === 'number' ? data.acceptedLatencyMs : typeof data.accepted_latency_ms === 'number' ? data.accepted_latency_ms : undefined,
    message
  }
}

function normalizeAiChatMessage(message: RawAiChatMessage): AiChatMessage {
  return {
    id: typeof message.id === 'string' ? message.id : typeof message.messageId === 'string' ? message.messageId : typeof message.message_id === 'string' ? message.message_id : undefined,
    role: message.role === 'user' ? 'user' : 'assistant',
    content: String(message.content || message.assistantContent || message.assistant_content || ''),
    source: typeof message.source === 'string' ? message.source : undefined,
    status: typeof message.status === 'string' ? message.status : undefined,
    taskId: typeof message.taskId === 'string' ? message.taskId : typeof message.task_id === 'string' ? message.task_id : undefined,
    conversationId: typeof message.conversationId === 'string' ? message.conversationId : typeof message.conversation_id === 'string' ? message.conversation_id : undefined,
    intent: typeof message.intent === 'string' ? message.intent : undefined,
    errorMessage: typeof message.errorMessage === 'string' ? message.errorMessage : typeof message.error_message === 'string' ? message.error_message : undefined,
    structuredResult: (message.structuredResult || message.structured_result || null) as StructuredResult | null
  }
}

export async function getAgentRuntimeHealth(session: AuthSession): Promise<AgentRuntimeHealth> {
  return requestJson<AgentRuntimeHealth>('/agent-runtime/health', { method: 'GET', session })
}

export function connectAgentProgress(session: AuthSession, conversationId: string, onEvent: (event: AgentProgressEvent) => void, onDisconnect?: () => void): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const params = new URLSearchParams({ token: session.token })
  if (session.user.defaultTenantId) params.set('tenant_id', session.user.defaultTenantId)
  if (session.workspace.currentShopId) params.set('shop_id', session.workspace.currentShopId)
  const socket = new WebSocket(`${protocol}//${window.location.host}${API_BASE}/ws/${encodeURIComponent(conversationId)}?${params.toString()}`)
  socket.onopen = () => socket.send(JSON.stringify({ type: 'hello', conversationId }))
  socket.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as AgentProgressEvent)
    } catch {
      onEvent({ type: 'raw', message: String(message.data) })
    }
  }
  socket.onclose = () => onDisconnect?.()
  socket.onerror = () => onDisconnect?.()
  return socket
}

export const loadMockSession = mockLoadSession
export const saveMockSession = mockSaveSession
