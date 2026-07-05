import { completeOnboarding as mockCompleteOnboarding, loadSession as mockLoadSession, loginWithMock, logoutMock, registerWithMock, saveSession as mockSaveSession } from './mockAuth'
import type { AuthSession, IntegrationStatus, LoginPayload, OnboardingPayload, RegisterPayload, ReportDetail, Shop, StrategyStatus, User, WorkspaceData } from '../types'

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

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  const text = await response.text()
  const data = parseResponseBody(text)
  if (response.status === 401) {
    logout()
    window.dispatchEvent(new CustomEvent('ecompilot:auth-expired'))
    throw new Error('登录已过期，请重新登录')
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
    plan: String(raw.plan || '团队版')
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
    try {
      const account = await requestJson<{ user: Partial<User> & Record<string, unknown> }>('/account/onboarding-completed', { method: 'POST', body: JSON.stringify({}), session })
      user = normalizeUser(account.user)
    } catch {
      // Brain 的引导已经完成；账号标记失败时保留前端会话态，避免阻断首次进入工作台。
    }
    const nextSession = { ...session, user, workspace: data.workspace }
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
  return refreshWorkspace(session)
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

export async function sendAiMessage(session: AuthSession, content: string): Promise<string> {
  const data = await requestJson<{ message: { content: string } }>('/ai-chat/messages', { method: 'POST', body: JSON.stringify({ content }), session })
  return data.message.content
}

export const loadMockSession = mockLoadSession
export const saveMockSession = mockSaveSession
