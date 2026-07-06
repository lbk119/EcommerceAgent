export type AuthMode = 'login' | 'register'

export type IntegrationStatus = 'unauthorized' | 'authorized' | 'expired' | 'syncing' | 'failed'

export type AgentStatus = 'idle' | 'working' | 'review' | 'error'

export type RiskLevel = 'low' | 'medium' | 'high'

export type StrategyStatus = 'pending' | 'accepted' | 'deferred' | 'rejected'

export interface User {
  id: string
  name: string
  email: string
  phone?: string
  companyName: string
  role: string
  createdAt: string
  onboardingCompleted: boolean
  plan: string
  tenantIds?: string[]
  shopIds?: string[]
  defaultTenantId?: string
  defaultShopId?: string
}

export interface Shop {
  id: string
  name: string
  category: string
  platform: string
  status: 'active' | 'paused' | 'setup'
  type: string
  businessStage: string
  lastSyncAt: string
  importStatus: string
}

export interface Integration {
  id: string
  platform: string
  status: IntegrationStatus
  lastSyncAt: string
  errorMessage?: string
}

export interface BusinessMetrics {
  date: string
  gmv: number
  orders: number
  conversionRate: number
  averageOrderValue: number
  refundRate: number
  visitors: number
  inventoryRiskSkuCount: number
  activeCampaignProducts: number
  aiCompletedTasks: number
}

export interface Product {
  id: string
  name: string
  sku: string
  category: string
  price: number
  stock: number
  safetyStock?: number
  sales: number
  conversionRate: number
  riskLevel: RiskLevel
  layer: '爆品' | '潜力品' | '稳定品' | '稳态品' | '滞销品'
  riskReason: string
  suggestedAction?: string
  aiSuggestion: string
}

export interface AgentTask {
  title: string
  status: '待执行' | '进行中' | '待审核' | '已完成'
  due: string
}

export interface AgentOutput {
  title: string
  type: string
  createdAt: string
}

export interface DigitalAgent {
  id: string
  name: string
  role: string
  responsibilities: string[]
  status: AgentStatus
  tasks: AgentTask[]
  outputs: AgentOutput[]
}

export interface Report {
  id: string
  type: string
  title: string
  summary: string
  createdAt: string
  status: 'draft' | 'ready' | 'archived'
}

export interface ReportDetail extends Report {
  contentMarkdown: string
  structuredResult?: StructuredResult | null
}

export interface StructuredResult {
  conclusion?: string
  evidence?: string[]
  actions?: string[]
  risks?: string[]
  missingData?: string[]
  stepSummaries?: Array<{ step?: string; label?: string; status?: string; summary?: string; latencyMs?: number; rows?: unknown[] }>
  latencyMs?: number
}

export interface Strategy {
  id: string
  title: string
  source: string
  expectedImpact: string
  riskLevel: RiskLevel
  status: StrategyStatus
  createdAt: string
}

export interface Campaign {
  id: string
  name: string
  score: number
  roi: number
  gmv: number
  conversionChange: number
  conclusion: string
}

export interface ImportRecord {
  id: string
  source: string
  fileName: string
  rows: number
  status: string
  createdAt: string
  qualityScore: number
}

export interface WorkspaceData {
  currentShopId: string
  shops: Shop[]
  integrations: Integration[]
  metrics: BusinessMetrics
  products: Product[]
  agents: DigitalAgent[]
  reports: Report[]
  strategies: Strategy[]
  campaigns: Campaign[]
  imports: ImportRecord[]
}

export interface AuthSession {
  token: string
  user: User
  workspace: WorkspaceData
}

export interface RegisterPayload {
  companyName: string
  name: string
  email: string
  password: string
  confirmPassword: string
}

export interface LoginPayload {
  account: string
  password: string
}

export interface OnboardingPayload {
  shopName: string
  category: string
  shopType: string
  businessStage: string
  selectedPlatforms: string[]
  dataMode: 'sample' | 'upload' | 'paste'
  enabledAgentIds: string[]
}