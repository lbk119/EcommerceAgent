<script setup lang="ts">
import { computed, defineComponent, h, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import type { PropType } from 'vue'
import { agentDefinitions, platformOptions } from './data/mockData'
import { analyzeProducts, completeOnboarding, confirmImportJob, connectAgentProgress, createOrUpdateShop, fetchAiChatMessages, generateReplenishmentPlan, generateReport, getAgentRuntimeHealth, getAiChatMessage, getAiChatTimeline, getReport, importSampleData, JobTimeoutError, listAiChatConversations, loadSession, login, logout as clearSession, pasteImportText, previewImportJob, refreshWorkspace, register, removeShop, reviewCampaign, saveSession, sendAiMessage, setIntegration, setStrategyStatus, startAgentJob, uploadImportFile, waitForJobAndRefresh } from './services/platformApi'
import type { AgentJob, AgentProgressEvent, AgentRuntimeHealth, AiChatTimelineEvent, ImportPreview } from './services/platformApi'
import type { AuthMode, AuthSession, DigitalAgent, Integration, IntegrationStatus, OnboardingPayload, Product, ReportDetail, Shop, StrategyStatus, StructuredResult } from './types'

type ChatMessage = {
  id?: string
  role: 'user' | 'assistant'
  content: string
  source?: 'agent' | 'agent_timeout' | 'error' | 'system' | string
  status?: 'queued' | 'running' | 'completed' | 'failed' | 'timeout' | 'cancelled' | string
  taskId?: string
  conversationId?: string
  intent?: string
  errorMessage?: string
  progressTimeline?: ChatProgressItem[]
  expandedTimeline?: boolean
  startedAt?: number
  updatedAt?: number
  totalLatencyMs?: number
  modelProfile?: string
  workflowName?: string
  deepAgentUsed?: boolean
  memoryStatus?: string
  structuredResult?: StructuredResult | null
  showRawMarkdown?: boolean
  originalContent?: string
  deltaKeys?: string[]
}

type ChatProgressItem = {
  eventType: string
  stage: string
  title: string
  detail?: string
  group: string
  timestamp?: string
  latencyMs?: number
  status?: string
  importance?: string
}

type RuntimeStatCard = { label: string; value: string; tone?: 'good' | 'warning' | 'danger' | 'neutral' }

const StructuredResultView = defineComponent({
  name: 'StructuredResultView',
  props: { result: { type: Object as PropType<StructuredResult | null>, required: false } },
  setup(props) {
    return () => {
      const result = props.result
      if (!hasStructuredResult(result)) return null
      const sections = [
        { key: 'actions', title: '建议动作', tone: 'good', items: structuredItems(result?.actions) },
        { key: 'evidence', title: '关键依据', tone: 'neutral', items: structuredItems(result?.evidence) },
        { key: 'risks', title: '风险点', tone: 'danger', items: structuredItems(result?.risks) },
        { key: 'missingData', title: '缺失数据', tone: 'warning', items: structuredItems(result?.missingData) }
      ].filter((section) => section.items.length)
      const steps = result?.stepSummaries || []
      return h('section', { class: 'structured-result' }, [
        result?.conclusion ? h('article', { class: 'structured-conclusion' }, [h('span', '结论'), h('strong', result.conclusion)]) : null,
        typeof result?.latencyMs === 'number' ? h('small', { class: 'structured-latency' }, `端到端 ${Math.round(result.latencyMs)}ms`) : null,
        sections.length ? h('div', { class: 'structured-grid' }, sections.map((section) => h('article', { class: ['structured-card', section.tone], key: section.key }, [h('h4', section.title), h('ul', section.items.map((item) => h('li', item)))]))) : null,
        steps.length ? h('div', { class: 'structured-steps' }, [h('h4', '执行明细'), ...steps.map((step, index) => h('div', { class: 'structured-step', key: `${step.step || step.label || index}` }, [h('strong', stepSummaryTitle(step)), h('span', step.summary || '已返回结构化结果'), stepSummaryMeta(step) ? h('small', stepSummaryMeta(step)) : null]))]) : null
      ])
    }
  }
})

const session = ref<AuthSession | null>(loadSession())
const currentPath = ref(window.location.pathname)
const authMode = ref<AuthMode>('login')
const authError = ref('')
const isAuthSubmitting = ref(false)
const isOnboardingSubmitting = ref(false)
const isWorkspaceRefreshing = ref(false)
const selectedAgentId = ref('')
const shopDraft = reactive({ name: '', category: '', platform: '淘宝 / 天猫', type: '品牌自营', businessStage: '成长期' })
const editingShopId = ref<string | null>(null)
const selectedImportMode = ref<'sample' | 'upload' | 'paste'>('sample')
const importNotice = ref('')
const pasteText = ref('')
const selectedImportFile = ref<File | null>(null)
const importPreview = ref<ImportPreview | null>(null)
const pendingImportJobId = ref('')
const isImporting = ref(false)
const activeJobNotice = ref('')
const activeJobError = ref('')
const isRunningJob = ref(false)
const updatingStrategyIds = ref(new Set<string>())
const selectedReportId = ref('')
const selectedReport = ref<ReportDetail | null>(null)
const showReportMarkdown = ref(false)
const chatDraft = ref('')
const isChatSending = ref(false)
const currentChatConversationId = ref('')
const chatSocketStatus = ref<'idle' | 'connecting' | 'connected' | 'disconnected'>('idle')
const activeChatTaskId = ref('')
const agentRuntimeHealth = ref<AgentRuntimeHealth | null>(null)
const chatMessageListRef = ref<HTMLElement | null>(null)
let chatSocket: WebSocket | null = null
let chatSocketConversationId = ''
const chatRecoveryTimers = new Map<string, number>()
const chatBackgroundNoticeTimers = new Map<string, number>()
const chatMessages = ref<ChatMessage[]>([
  { role: 'assistant', source: 'system', content: '我是 EcomPilot 辅助 AI 助手。你可以直接问经营数据、商品优化、库存风险、活动复盘或报告写作问题。' }
])

const loginForm = reactive({ account: 'operator@example.com', password: 'admin123' })
const registerForm = reactive({ companyName: '', name: '', email: '', password: '', confirmPassword: '' })
const onboarding = reactive<OnboardingPayload>({
  shopName: '',
  category: '服饰鞋包',
  shopType: '品牌自营',
  businessStage: '成长期',
  selectedPlatforms: ['淘宝 / 天猫', '抖音电商'],
  dataMode: 'upload',
  enabledAgentIds: agentDefinitions.map((agent) => agent.id)
})
const onboardingStep = ref(1)

const workspace = computed(() => session.value?.workspace)
const user = computed(() => session.value?.user)
const currentShop = computed(() => workspace.value?.shops.find((shop) => shop.id === workspace.value?.currentShopId) || workspace.value?.shops[0])
const selectedAgent = computed(() => workspace.value?.agents.find((agent) => agent.id === selectedAgentId.value) || workspace.value?.agents[0])
const highRiskProducts = computed(() => workspace.value?.products.filter((product) => product.riskLevel === 'high') || [])
const topRiskProducts = computed(() => highRiskProducts.value.slice(0, 5))
const pendingStrategies = computed(() => workspace.value?.strategies.filter((strategy) => strategy.status === 'pending') || [])
const authorizedIntegrations = computed(() => workspace.value?.integrations.filter((item) => item.status === 'authorized' || item.status === 'syncing') || [])
const taskCount = computed(() => workspace.value?.agents.reduce((sum, agent) => sum + agent.tasks.filter((task) => task.status !== '已完成').length, 0) || 0)
const hasBusinessData = computed(() => {
  const metrics = workspace.value?.metrics
  return Boolean(metrics && (metrics.gmv > 0 || metrics.orders > 0 || metrics.visitors > 0 || metrics.activeCampaignProducts > 0 || metrics.inventoryRiskSkuCount > 0))
})
const selectedReportContent = computed(() => selectedReport.value?.contentMarkdown?.trim() || selectedReport.value?.summary || '报告内容生成中，请稍后刷新。')
const selectedReportHtml = computed(() => renderMarkdown(selectedReportContent.value))
const selectedReportStructured = computed(() => selectedReport.value?.structuredResult || null)
const activeChatMessage = computed(() => chatMessages.value.find((message) => message.role === 'assistant' && message.taskId === activeChatTaskId.value))
const activeChatStage = computed(() => {
  const timeline = activeChatMessage.value?.progressTimeline || []
  return timeline.length ? timeline[timeline.length - 1]!.title : '等待任务'
})
const activeChatElapsedText = computed(() => formatElapsed(activeChatMessage.value))
const runtimeCards = computed<RuntimeStatCard[]>(() => [
  { label: '当前任务', value: statusLabel(activeChatMessage.value?.status || (activeChatTaskId.value ? 'running' : 'idle')), tone: activeChatMessage.value?.status === 'failed' ? 'danger' : activeChatMessage.value?.status === 'completed' ? 'good' : 'neutral' },
  { label: '当前阶段', value: activeChatStage.value },
  { label: '已耗时', value: activeChatElapsedText.value },
  { label: '工作流', value: activeChatMessage.value?.workflowName || (activeChatMessage.value?.source === 'workflow' ? '已命中' : '待判断') },
  { label: '模型', value: activeChatMessage.value?.modelProfile || 'fast' },
  { label: 'DeepAgent', value: activeChatMessage.value?.deepAgentUsed ? '已调用' : '未调用' },
  { label: '记忆写入', value: activeChatMessage.value?.memoryStatus || '后台处理' },
  { label: 'WebSocket', value: chatSocketStatus.value === 'connected' ? '已连接' : chatSocketStatus.value, tone: chatSocketStatus.value === 'connected' ? 'good' : 'warning' }
])

const navItems = [
  { path: '/dashboard', label: '工作台', hint: 'Dashboard' },
  { path: '/agents', label: '数字员工', hint: 'Agents' },
  { path: '/reports', label: '经营报告', hint: 'Reports' },
  { path: '/products', label: '商品分析', hint: 'Products' },
  { path: '/inventory', label: '库存风险', hint: 'Inventory' },
  { path: '/campaigns', label: '活动复盘', hint: 'Campaigns' },
  { path: '/ai-chat', label: 'AI 对话', hint: 'Assistant' },
  { path: '/shops', label: '店铺管理', hint: 'Shops' },
  { path: '/integrations', label: '平台授权', hint: 'Integrations' },
  { path: '/data-import', label: '数据导入', hint: 'Data Import' },
  { path: '/account', label: '个人中心', hint: 'Account' }
]

const metricCards = computed(() => {
  const metrics = workspace.value?.metrics
  if (!metrics) return []
  const emptyTrend = hasBusinessData.value ? '等待对比数据' : '待导入数据'
  return [
    { label: '成交额（GMV）', value: money(metrics.gmv), trend: emptyTrend },
    { label: '订单数', value: metrics.orders.toLocaleString(), trend: emptyTrend },
    { label: '访客下单比例', value: `${metrics.conversionRate}%`, trend: emptyTrend },
    { label: '平均每单金额', value: money(metrics.averageOrderValue), trend: emptyTrend },
    { label: '退款率', value: `${metrics.refundRate}%`, trend: emptyTrend },
    { label: '库存风险商品编码（SKU）', value: String(metrics.inventoryRiskSkuCount), trend: metrics.inventoryRiskSkuCount > 0 ? `${metrics.inventoryRiskSkuCount} 个风险` : '暂无风险' },
    { label: '活动中商品', value: String(metrics.activeCampaignProducts), trend: metrics.activeCampaignProducts > 0 ? '活动进行中' : '暂无活动' },
    { label: 'AI 已完成巡检', value: String(metrics.aiCompletedTasks), trend: metrics.aiCompletedTasks > 0 ? '今日任务' : '待启动' }
  ]
})

const statusText: Record<IntegrationStatus, string> = {
  unauthorized: '未授权',
  authorized: '已授权',
  expired: '授权过期',
  syncing: '同步中',
  failed: '同步失败'
}

const agentStatusText: Record<DigitalAgent['status'], string> = {
  idle: '空闲',
  working: '工作中',
  review: '等待审核',
  error: '异常'
}

const reportTypeText: Record<string, string> = {
  daily: '经营日报',
  weekly: '经营周报',
  monthly: '经营月报',
  inventory: '库存风险',
  campaign: '活动复盘',
  product: '商品分析'
}

function money(value: number) {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 0 }).format(value)
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = { idle: '暂无任务', queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', timeout: '后台继续', cancelled: '已取消' }
  return labels[String(status || 'idle')] || String(status)
}

function formatElapsed(message?: ChatMessage) {
  if (!message?.startedAt) return '0.0s'
  const elapsedMs = message.totalLatencyMs || ((message.updatedAt || Date.now()) - message.startedAt)
  return `${(elapsedMs / 1000).toFixed(1)}s`
}

function reportTypeLabel(type: string) {
  return reportTypeText[type] || type
}

function navigate(path: string) {
  window.history.pushState({}, '', path)
  currentPath.value = path
  if (path.startsWith('/agents/')) selectedAgentId.value = path.split('/').pop() || ''
  enforceRouteGuard()
}

function enforceRouteGuard() {
  const isAuthenticated = Boolean(session.value)
  const isAuthPage = currentPath.value === '/' || currentPath.value === '/login'
  if (!isAuthenticated && !isAuthPage) {
    window.history.replaceState({}, '', '/login')
    currentPath.value = '/login'
    return
  }
  if (!isAuthenticated && currentPath.value === '/') {
    window.history.replaceState({}, '', '/login')
    currentPath.value = '/login'
    return
  }
  if (isAuthenticated && !session.value?.user.onboardingCompleted && currentPath.value !== '/onboarding') {
    window.history.replaceState({}, '', '/onboarding')
    currentPath.value = '/onboarding'
    return
  }
  if (isAuthenticated && session.value?.user.onboardingCompleted && isAuthPage) {
    window.history.replaceState({}, '', '/dashboard')
    currentPath.value = '/dashboard'
  }
}

async function submitLogin() {
  if (isAuthSubmitting.value) return
  authError.value = ''
  isAuthSubmitting.value = true
  try {
    session.value = await login(loginForm)
    navigate(session.value.user.onboardingCompleted ? '/dashboard' : '/onboarding')
    if (session.value.user.onboardingCompleted) loadWorkspaceInBackground(session.value, '正在加载工作台数据...')
  } catch (error) {
    const message = error instanceof Error ? error.message : '登录失败'
    authError.value = /用户名或密码|账号或密码|invalid credentials/i.test(message) ? '账号或密码错误' : message
  } finally {
    isAuthSubmitting.value = false
  }
}

function loadWorkspaceInBackground(currentSession: AuthSession, loadingText: string) {
  if (isWorkspaceRefreshing.value) return
  isWorkspaceRefreshing.value = true
  activeJobNotice.value = loadingText
  activeJobError.value = ''
  refreshWorkspace(currentSession).then((nextSession) => {
    session.value = nextSession
    activeJobNotice.value = ''
  }).catch((error) => {
    activeJobNotice.value = ''
    activeJobError.value = error instanceof Error ? `工作台加载失败：${error.message}` : '工作台加载失败'
  }).finally(() => {
    isWorkspaceRefreshing.value = false
  })
}

async function submitRegister() {
  if (isAuthSubmitting.value) return
  authError.value = ''
  isAuthSubmitting.value = true
  try {
    session.value = await register(registerForm)
    navigate('/onboarding')
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '注册失败'
  } finally {
    isAuthSubmitting.value = false
  }
}

async function finishOnboarding() {
  if (!session.value || isOnboardingSubmitting.value) return
  isOnboardingSubmitting.value = true
  activeJobError.value = ''
  try {
    const shouldImportSample = onboarding.dataMode === 'sample'
    session.value = await completeOnboarding(session.value, onboarding)
    navigate('/dashboard')
    isWorkspaceRefreshing.value = true
    activeJobNotice.value = shouldImportSample ? '正在后台生成示例数据，完成后会自动刷新工作台。' : '工作台初始化完成，正在刷新数据。'
    const currentSession = session.value
    const refreshPromise = shouldImportSample ? importSampleData(currentSession) : refreshWorkspace(currentSession).then((nextSession) => ({ session: nextSession, job: null }))
    refreshPromise.then((result) => {
      session.value = result.session
      activeJobNotice.value = shouldImportSample ? '示例数据已生成，工作台已刷新。' : '工作台数据已刷新。'
    }).catch((error) => {
      activeJobError.value = error instanceof Error ? error.message : '工作台刷新失败'
    }).finally(() => {
      isWorkspaceRefreshing.value = false
    })
  } catch (error) {
    activeJobError.value = error instanceof Error ? error.message : '初始化失败'
  } finally {
    isOnboardingSubmitting.value = false
  }
}

function toggleOnboardingPlatform(platform: string) {
  const index = onboarding.selectedPlatforms.indexOf(platform)
  if (index >= 0) onboarding.selectedPlatforms.splice(index, 1)
  else onboarding.selectedPlatforms.push(platform)
}

function toggleOnboardingAgent(agentId: string) {
  const index = onboarding.enabledAgentIds.indexOf(agentId)
  if (index >= 0) onboarding.enabledAgentIds.splice(index, 1)
  else onboarding.enabledAgentIds.push(agentId)
}

async function switchShop(shopId: string) {
  if (!workspace.value || !session.value) return
  workspace.value.currentShopId = shopId
  saveSession(session.value)
  try {
    session.value = await refreshWorkspace(session.value)
  } catch {
    saveSession(session.value)
  }
}

function startEditShop(shop: Shop) {
  editingShopId.value = shop.id
  Object.assign(shopDraft, { name: shop.name, category: shop.category, platform: shop.platform, type: shop.type, businessStage: shop.businessStage })
}

async function saveShop() {
  if (!workspace.value || !session.value || !shopDraft.name.trim()) return
  session.value = await createOrUpdateShop(session.value, shopDraft, editingShopId.value || undefined)
  editingShopId.value = null
  Object.assign(shopDraft, { name: '', category: '', platform: '淘宝 / 天猫', type: '品牌自营', businessStage: '成长期' })
}

async function deleteShop(shopId: string) {
  if (!workspace.value || !session.value || workspace.value.shops.length <= 1) return
  const previousSession = session.value
  const nextShops = previousSession.workspace.shops.filter((shop) => shop.id !== shopId)
  const nextCurrentShopId = previousSession.workspace.currentShopId === shopId ? nextShops[0]?.id || '' : previousSession.workspace.currentShopId
  session.value = { ...previousSession, workspace: { ...previousSession.workspace, shops: nextShops, currentShopId: nextCurrentShopId } }
  saveSession(session.value)
  removeShop(previousSession, shopId).catch(() => {
    session.value = previousSession
    saveSession(previousSession)
  })
}

async function setIntegrationStatus(integration: Integration, nextStatus: IntegrationStatus) {
  if (!session.value) return
  session.value = await setIntegration(session.value, integration.platform, nextStatus)
}

async function updateStrategy(strategyId: string, status: StrategyStatus) {
  if (!workspace.value || !session.value) return
  if (updatingStrategyIds.value.has(strategyId)) return
  const previousStrategies = [...workspace.value.strategies]
  updatingStrategyIds.value = new Set([...updatingStrategyIds.value, strategyId])
  session.value = { ...session.value, workspace: { ...workspace.value, strategies: workspace.value.strategies.map((strategy) => strategy.id === strategyId ? { ...strategy, status } : strategy) } }
  saveSession(session.value)
  try {
    session.value = await setStrategyStatus(session.value, strategyId, status)
  } catch (error) {
    session.value = { ...session.value, workspace: { ...session.value.workspace, strategies: previousStrategies } }
    saveSession(session.value)
    activeJobError.value = error instanceof Error ? error.message : '策略更新失败'
  } finally {
    const next = new Set(updatingStrategyIds.value)
    next.delete(strategyId)
    updatingStrategyIds.value = next
  }
}

async function runAgent(agent: DigitalAgent) {
  await runTrackedJob((currentSession) => startAgentJob(currentSession, agent.id, `${agent.name}巡检`), '数字员工任务已启动')
}

async function completeAgentOutput(agent: DigitalAgent, title: string) {
  const reportTypeByAgent: Record<string, string> = { 'store-analyst': 'daily', 'product-assistant': 'product', 'inventory-inspector': 'inventory', 'campaign-reviewer': 'campaign', 'report-specialist': 'weekly' }
  await runTrackedJob(async (currentSession) => (await generateReport(currentSession, reportTypeByAgent[agent.id] || 'daily', title, agent.id)).job, '报告生成任务已启动')
}

async function runTrackedJob(factory: (currentSession: AuthSession) => Promise<AgentJob>, startedText: string) {
  if (!session.value || isRunningJob.value) return
  activeJobError.value = ''
  activeJobNotice.value = startedText
  isRunningJob.value = true
  try {
    const job = await factory(session.value)
    activeJobNotice.value = '数字员工任务已启动，正在分析数据…'
    session.value = await waitForJobAndRefresh(session.value, job.jobId || job.id)
    activeJobNotice.value = '报告已生成，工作台数据已刷新。'
  } catch (error) {
    if (error instanceof JobTimeoutError) activeJobNotice.value = error.message
    else activeJobError.value = `任务失败：${error instanceof Error ? error.message : '未知错误'}`
  } finally {
    isRunningJob.value = false
  }
}

async function runWeeklyReport() {
  await runTrackedJob(async (currentSession) => (await generateReport(currentSession, 'weekly', '管理层经营周报', 'report-specialist')).job, '管理层周报任务已启动')
}

async function runProductAnalysis() {
  await runTrackedJob(analyzeProducts, '商品优化任务已启动')
}

async function runReplenishmentPlan() {
  await runTrackedJob(generateReplenishmentPlan, '补货建议任务已启动')
}

async function runCampaignReview() {
  const campaign = workspace.value?.campaigns[0]
  if (!campaign) {
    activeJobNotice.value = ''
    activeJobError.value = '任务失败：当前没有可复盘的活动数据'
    return
  }
  await runTrackedJob((currentSession) => reviewCampaign(currentSession, campaign.id), '活动复盘任务已启动')
}

async function openReportDetail(reportId: string) {
  if (!session.value) return
  selectedReportId.value = reportId
  showReportMarkdown.value = false
  selectedReport.value = await getReport(session.value, reportId)
}

async function useSampleData() {
  if (!workspace.value || !session.value) return
  const result = await importSampleData(session.value)
  session.value = result.session
  importNotice.value = buildImportSuccessNotice(result)
}

function buildImportSuccessNotice(result: { session: AuthSession; job: { generatedReportId?: string; generatedStrategiesCount?: number; rows?: number } }) {
  const nextWorkspace = result.session.workspace
  const metrics = nextWorkspace.metrics
  return `数据已确认入库${result.job.rows ? ` ${result.job.rows} 行` : ''}，已生成经营概览报告${result.job.generatedReportId ? ' 1 条' : ' 0 条'}、待审核策略 ${result.job.generatedStrategiesCount || 0} 条；工作台已刷新：订单 ${metrics.orders} 单、商品 ${nextWorkspace.products.length} 个、库存风险 ${metrics.inventoryRiskSkuCount} 个。`
}

function handleImportFileChange(event: Event) {
  const files = (event.target as HTMLInputElement).files
  selectedImportFile.value = files?.[0] || null
  importPreview.value = null
  pendingImportJobId.value = ''
  importNotice.value = selectedImportFile.value ? `已选择文件：${selectedImportFile.value.name}` : ''
}

async function executeImport() {
  if (!session.value) return
  importNotice.value = ''
  if (selectedImportMode.value === 'sample') {
    await useSampleData()
    return
  }
  if (selectedImportMode.value === 'paste') {
    if (!pasteText.value.trim()) {
      importNotice.value = '请先粘贴 CSV 或 TSV 格式数据。'
      return
    }
    isImporting.value = true
    try {
      const job = await pasteImportText(session.value, pasteText.value)
      pendingImportJobId.value = job.id
      importPreview.value = await previewImportJob(session.value, job.id)
      importNotice.value = `粘贴数据已解析，识别到 ${importPreview.value.rows.length} 行预览数据。确认无误后点击“确认入库”。`
    } catch (error) {
      importNotice.value = error instanceof Error ? error.message : '粘贴数据解析失败'
    } finally {
      isImporting.value = false
    }
    return
  }
  if (!selectedImportFile.value) {
    importNotice.value = '请先选择一个 CSV 文件，再执行导入。'
    return
  }
  isImporting.value = true
  try {
    const job = await uploadImportFile(session.value, selectedImportFile.value)
    pendingImportJobId.value = job.id
    importPreview.value = await previewImportJob(session.value, job.id)
    importNotice.value = `文件 ${job.fileName} 已上传，识别到 ${importPreview.value.rows.length} 行预览数据。确认无误后点击“确认入库”。`
  } catch (error) {
    importNotice.value = error instanceof Error ? error.message : '文件上传失败'
  } finally {
    isImporting.value = false
  }
}

async function confirmUploadedImport() {
  if (!session.value || !pendingImportJobId.value) return
  isImporting.value = true
  try {
    const result = await confirmImportJob(session.value, pendingImportJobId.value)
    session.value = result.session
    importNotice.value = buildImportSuccessNotice(result)
    pendingImportJobId.value = ''
    importPreview.value = null
    selectedImportFile.value = null
  } catch (error) {
    importNotice.value = error instanceof Error ? error.message : '确认入库失败'
  } finally {
    isImporting.value = false
  }
}

async function sendChatMessage() {
  const content = chatDraft.value.trim()
  if (!content || !session.value || isChatSending.value) return
  chatMessages.value.push({ role: 'user', content })
  scrollChatToBottom()
  chatDraft.value = ''
  isChatSending.value = true
  try {
    const accepted = await sendAiMessage(session.value, content, currentChatConversationId.value || undefined)
    currentChatConversationId.value = accepted.conversationId
    activeChatTaskId.value = accepted.taskId
    const assistantMessage: ChatMessage = {
      id: accepted.messageId,
      role: 'assistant',
      source: accepted.source || 'agent',
      status: accepted.status,
      taskId: accepted.taskId,
      conversationId: accepted.conversationId,
      intent: accepted.intent,
      content: accepted.message?.content || 'Agent 已接收任务，正在进入运行队列。',
      startedAt: Date.now(),
      updatedAt: Date.now(),
      modelProfile: 'fast',
      originalContent: content,
      progressTimeline: [{ eventType: 'accepted', stage: 'queued', group: '接收问题', title: '已收到问题', detail: 'Agent 已接收任务并进入运行队列。', status: 'running', importance: 'high' }]
    }
    chatMessages.value.push(assistantMessage)
    connectChatProgress(assistantMessage)
    scheduleChatBackgroundNotice(assistantMessage)
    startChatTimelineRecovery(assistantMessage)
    scrollChatToBottom()
  } catch (error) {
    chatMessages.value.push({ role: 'assistant', source: 'error', status: 'failed', content: '后端 Agent 暂不可用，无法完成智能分析。请稍后重试。', errorMessage: error instanceof Error ? error.message : undefined, originalContent: content })
    scrollChatToBottom()
  } finally {
    isChatSending.value = false
  }
}

function connectChatProgress(message: ChatMessage) {
  if (!session.value || !message.conversationId) return
  if (chatSocket && chatSocketConversationId === message.conversationId && chatSocket.readyState !== WebSocket.CLOSED) return
  chatSocket?.close()
  chatSocketStatus.value = 'connecting'
  chatSocketConversationId = message.conversationId
  chatSocket = connectAgentProgress(session.value, message.conversationId, (event) => handleAgentProgressEvent(event), () => {
    const runningMessages = chatMessages.value.filter((item) => item.role === 'assistant' && item.conversationId === message.conversationId && item.taskId && item.status === 'running')
    if (runningMessages.length) {
      chatSocketStatus.value = 'disconnected'
      for (const runningMessage of runningMessages) {
        appendChatProgress(runningMessage, 'ws_disconnected', 'recovery', '恢复进度', '实时连接断开', '正在轮询补偿。', undefined, 'running', 'normal')
        startChatTimelineRecovery(runningMessage)
      }
    }
  })
  chatSocket.onopen = () => {
    chatSocketStatus.value = 'connected'
    chatSocket?.send(JSON.stringify({ type: 'hello', conversationId: message.conversationId }))
  }
}

function handleAgentProgressEvent(event: AgentProgressEvent) {
  if (event.type === 'pong') return
  const message = findChatMessageForEvent(event)
  if (!message) return
  if (event.type === 'assistant_delta') {
    applyAssistantDelta(message, event)
    message.updatedAt = Date.now()
    scrollChatToBottom()
    return
  }
  if (event.type === 'assistant_final') {
    message.content = event.content || message.content
    message.status = 'completed'
    message.source = event.source || message.source || 'workflow_fast'
    message.totalLatencyMs = event.totalLatencyMs
    message.updatedAt = Date.now()
    stopChatTimelineRecovery(message)
    clearChatBackgroundNotice(message)
    if (activeChatTaskId.value === message.taskId) activeChatTaskId.value = ''
    appendChatProgress(message, 'assistant_final', 'completed', '完成', '结果已生成', undefined, event.totalLatencyMs, 'completed', 'high')
    refreshChatMessage(message).catch(() => undefined)
    scrollChatToBottom()
    return
  }
  if (event.type === 'agent_error') {
    message.status = 'failed'
    message.errorMessage = event.errorMessage || 'Agent 执行失败'
    message.content = message.errorMessage
    message.source = 'error'
    stopChatTimelineRecovery(message)
    clearChatBackgroundNotice(message)
    if (activeChatTaskId.value === message.taskId) activeChatTaskId.value = ''
    appendChatProgress(message, 'agent_error', 'failed', '失败', message.errorMessage, undefined, event.latencyMs, 'failed', 'high')
    scrollChatToBottom()
    return
  }
  const eventType = event.eventType || event.event || event.type || 'progress'
  const data = event.data || {}
  const stageUpdate = stageFromEvent(event, data)
  appendChatProgress(message, stageUpdate.eventType, stageUpdate.stage, stageUpdate.group, stageUpdate.title, stageUpdate.detail, stageUpdate.latencyMs, stageUpdate.status, stageUpdate.importance, stageUpdate.timestamp)
  message.updatedAt = Date.now()
  if (stageUpdate.modelProfile) message.modelProfile = stageUpdate.modelProfile
  if (stageUpdate.workflowName) message.workflowName = stageUpdate.workflowName
  if (stageUpdate.deepAgentUsed) message.deepAgentUsed = true
  if (stageUpdate.memoryStatus) message.memoryStatus = stageUpdate.memoryStatus
  if (eventType === 'task_classified') {
    const taskPlan = data.task_plan as Record<string, unknown> | undefined
    const intent = taskPlan?.intent as Record<string, unknown> | undefined
    message.intent = String(data.intent || taskPlan?.primary_task_type || intent?.primary_goal || message.intent || '')
  }
  if (eventType === 'workflow_route_decided' && (data.workflow_name || stageUpdate.workflowName)) {
    message.source = 'workflow_fast'
  }
  if (eventType === 'agent_finished') {
    refreshChatMessage(message)
  }
}

function appendChatProgress(message: ChatMessage, eventType: string, stage: string, group: string, title: string, detail?: string, latencyMs?: number, status?: string, importance?: string, timestamp?: string) {
  const timeline = message.progressTimeline || []
  const existing = timeline.find((item) => item.eventType === eventType && item.stage === stage && item.title === title)
  if (existing) {
    existing.detail = detail || existing.detail
    existing.latencyMs = latencyMs ?? existing.latencyMs
    existing.status = status || existing.status
    existing.timestamp = timestamp || existing.timestamp
    return
  }
  const startedIndex = timeline.findIndex((item) => item.stage === stage && item.eventType.endsWith('_started') && eventType.endsWith('_finished'))
  if (startedIndex >= 0) {
    timeline[startedIndex] = { ...timeline[startedIndex]!, eventType, title, detail, latencyMs, status: status || 'completed', timestamp, importance }
    message.progressTimeline = [...timeline]
    return
  }
  timeline.push({ eventType, stage, group, title, detail, timestamp, latencyMs, status: status || message.status, importance })
  message.progressTimeline = timeline.slice(-24)
}

function scheduleChatBackgroundNotice(message: ChatMessage) {
  clearChatBackgroundNotice(message)
  const timer = window.setTimeout(() => {
    if (message.status === 'running') {
      message.content = '仍在深度分析，你可以继续浏览其他页面。完成后会自动更新。'
      appendChatProgress(message, 'background_continues', 'background', '后台继续', '仍在深度分析', '你可以继续浏览其他页面，完成后会自动更新。', undefined, 'running', 'high')
    }
  }, 10000)
  chatBackgroundNoticeTimers.set(chatMessageKey(message), timer)
}

function startChatTimelineRecovery(message: ChatMessage) {
  if (!session.value || !message.taskId) return
  stopChatTimelineRecovery(message)
  const timer = window.setInterval(() => {
    if (!session.value || !message.taskId || ['completed', 'failed', 'timeout', 'cancelled'].includes(String(message.status))) {
      stopChatTimelineRecovery(message)
      return
    }
    getAiChatTimeline(session.value, message.taskId).then((timeline) => {
      applyTimelineEvents(message, timeline.events || [])
      return refreshChatMessage(message)
    }).catch(() => {
      appendChatProgress(message, 'timeline_retry_failed', 'recovery', '恢复进度', '进度补偿拉取失败', '稍后自动重试。', undefined, 'running', 'normal')
    })
  }, 2500)
  chatRecoveryTimers.set(chatMessageKey(message), timer)
}

function stopChatTimelineRecovery(message?: ChatMessage) {
  if (message) {
    const key = chatMessageKey(message)
    const timer = chatRecoveryTimers.get(key)
    if (timer) window.clearInterval(timer)
    chatRecoveryTimers.delete(key)
    return
  }
  for (const timer of chatRecoveryTimers.values()) window.clearInterval(timer)
  chatRecoveryTimers.clear()
}

function clearChatBackgroundNotice(message?: ChatMessage) {
  if (message) {
    const key = chatMessageKey(message)
    const timer = chatBackgroundNoticeTimers.get(key)
    if (timer) window.clearTimeout(timer)
    chatBackgroundNoticeTimers.delete(key)
    return
  }
  for (const timer of chatBackgroundNoticeTimers.values()) window.clearTimeout(timer)
  chatBackgroundNoticeTimers.clear()
}

function findChatMessageForEvent(event: AgentProgressEvent) {
  const data = event.data || {}
  const messageId = stringValue(event.messageId || data.messageId || data.message_id)
  const taskId = stringValue(event.taskId || data.taskId || data.task_id)
  if (messageId) {
    const byMessage = chatMessages.value.find((message) => message.role === 'assistant' && message.id === messageId)
    if (byMessage) return byMessage
  }
  if (taskId) {
    const byTask = chatMessages.value.find((message) => message.role === 'assistant' && message.taskId === taskId)
    if (byTask) return byTask
  }
  return chatMessages.value.find((message) => message.role === 'assistant' && message.status === 'running')
}

function applyAssistantDelta(message: ChatMessage, event: AgentProgressEvent) {
  if (message.status === 'completed') return
  const delta = stringValue(event.delta || event.content || event.data?.delta)
  if (!delta) return
  const key = `${delta.length}:${delta.slice(0, 32)}:${delta.slice(-32)}`
  const keys = message.deltaKeys || []
  if (keys.includes(key) || message.content.includes(delta)) return
  message.deltaKeys = [...keys.slice(-20), key]
  if (isChatPlaceholder(message.content)) {
    message.content = delta
  } else {
    message.content = `${message.content}${delta}`
  }
}

function chatMessageKey(message: ChatMessage) {
  return message.id || message.taskId || `${message.conversationId || 'chat'}:${chatMessages.value.indexOf(message)}`
}

function isChatPlaceholder(content: string) {
  return content === 'Agent 已接收任务，正在进入运行队列。' || content === '仍在深度分析，你可以继续浏览其他页面。完成后会自动更新。'
}

function scrollChatToBottom() {
  nextTick(() => {
    const list = chatMessageListRef.value
    if (list) list.scrollTop = list.scrollHeight
  })
}

function applyTimelineEvents(message: ChatMessage, events: AiChatTimelineEvent[]) {
  for (const item of events.slice(-12)) {
    const stageUpdate = stageFromTimeline(item)
    appendChatProgress(message, stageUpdate.eventType, stageUpdate.stage, stageUpdate.group, stageUpdate.title, stageUpdate.detail, stageUpdate.latencyMs, stageUpdate.status, stageUpdate.importance, stageUpdate.timestamp)
  }
}

function stageFromTimeline(item: AiChatTimelineEvent) {
  return stageFromEvent({ type: 'agent_progress', eventType: item.event_type, title: undefined, timestamp: item.timestamp, latencyMs: item.latency_ms }, {
    workflow_name: item.workflow_name,
    step_name: item.step_name,
    latency_ms: item.latency_ms,
    error: item.error
  })
}

function stageFromEvent(event: AgentProgressEvent, data: Record<string, unknown>) {
  const eventType = event.eventType || event.event || event.type || 'progress'
  const rawStage = String(event.stage || data.stage || eventType)
  const workflowName = stringValue(data.workflow_name || data.workflowName)
  const modelProfile = stringValue(data.model_profile || data.modelProfile || data.profile)
  const latencyMs = typeof event.latencyMs === 'number' ? event.latencyMs : typeof data.latency_ms === 'number' ? data.latency_ms : undefined
  const base = stageDisplay(eventType, rawStage, data, event)
  return {
    eventType,
    stage: base.stage,
    group: event.display?.group || base.group,
    title: event.title || base.title,
    detail: event.detail || base.detail,
    status: event.status || base.status,
    importance: event.display?.importance || base.importance,
    timestamp: event.timestamp,
    latencyMs,
    workflowName,
    modelProfile,
    deepAgentUsed: eventType.includes('deep_agent') || eventType === 'deepagent_fallback_started' || base.stage === 'deep_agent',
    memoryStatus: base.stage === 'memory_write' ? (base.status === 'completed' ? '已完成' : base.status === 'skipped' ? '已跳过' : '后台处理') : undefined
  }
}

// 把后端 trace 的工程事件压缩成用户能理解的 8 个固定阶段，避免 raw event 直接把聊天气泡刷屏。
function stageDisplay(eventType: string, rawStage: string, data: Record<string, unknown>, event: AgentProgressEvent) {
  if (eventType === 'queued' || eventType === 'accepted') return { stage: 'queued', group: '接收问题', title: '已接收问题', detail: '任务已进入 Agent 队列。', status: 'running', importance: 'high' }
  if (eventType === 'task_classified') return { stage: 'intent', group: '识别意图', title: '已识别意图', detail: intentDetail(data), status: 'completed', importance: 'high' }
  if (eventType === 'context_prepared' || rawStage.includes('context')) return { stage: 'context', group: '读取店铺数据', title: '运行上下文已准备', detail: '已绑定租户、店铺和用户上下文。', status: 'completed', importance: 'normal' }
  if (eventType.startsWith('memory_retrieval') || eventType === 'memory_retrieved') return { stage: 'memory', group: '读取店铺数据', title: '长期记忆读取完成', detail: `召回 ${String(data.count || 0)} 条历史经验。`, status: eventType.endsWith('started') ? 'running' : 'completed', importance: 'low' }
  if (eventType === 'workflow_route_decided') return { stage: 'workflow_route', group: '命中工作流', title: data.workflow_name ? `已命中 ${String(data.workflow_name)}` : '未命中固定工作流', detail: data.workflow_name ? '使用快速 workflow 读取真实店铺数据。' : '当前问题需要更开放的推理。', status: 'completed', importance: 'high' }
  if (eventType === 'plan_execution_started') return { stage: 'plan', group: '执行计划', title: '已生成固定 DAG', detail: `准备并行执行 ${String(data.step_count || 0)} 个节点。`, status: 'running', importance: 'high' }
  if (eventType === 'plan_execution_finished') return { stage: 'plan', group: '执行计划', title: '并行 DAG 执行完成', detail: data.critical_failed ? '存在关键节点失败，已进入降级或 fallback 判断。' : '所有可用节点已返回结构化结果。', status: data.critical_failed ? 'failed' : 'completed', importance: 'high' }
  if (eventType.startsWith('plan_step')) return { stage: `workflow:${String(data.step_name || data.step_key || 'data')}`, group: '并行读取', title: eventType.endsWith('started') ? `并行读取 ${String(data.step_name || '业务数据')}` : eventType.endsWith('failed') ? `${String(data.step_name || '业务数据')} 读取失败` : `${String(data.step_name || '业务数据')} 已读取`, detail: eventType.endsWith('failed') ? '该并行节点执行失败，非关键节点不会阻塞整体结果。' : String(data.summary || '已返回结构化 JSON。'), status: eventType.endsWith('failed') ? 'failed' : eventType.endsWith('started') ? 'running' : 'completed', importance: eventType.endsWith('failed') && data.critical ? 'high' : 'normal' }
  if (eventType.startsWith('workflow_step')) return { stage: `workflow:${String(data.step_name || 'data')}`, group: '读取店铺数据', title: eventType.endsWith('started') ? `读取 ${String(data.step_name || '业务数据')}` : `${String(data.step_name || '业务数据')} 已读取`, detail: eventType.endsWith('failed') ? '该数据节点执行失败。' : '已从数据库读取并整理关键指标。', status: eventType.endsWith('failed') ? 'failed' : eventType.endsWith('started') ? 'running' : 'completed', importance: eventType.endsWith('failed') ? 'high' : 'normal' }
  if (eventType.startsWith('reducer')) return { stage: 'reducer', group: '汇总建议', title: eventType === 'reducer_started' ? '正在汇总并行结果' : eventType === 'reducer_finished' ? '汇总完成' : eventType === 'reducer_polish_started' ? 'fast model 润色中' : eventType === 'reducer_polish_finished' ? '润色完成' : '润色失败，返回确定性结论', detail: eventType.includes('polish') ? '模型只做表达润色，超时不影响确定性结果。' : '已将结构化 JSON 汇总为 conclusion/evidence/actions/risks。', status: eventType.endsWith('failed') ? 'failed' : eventType.endsWith('started') ? 'running' : 'completed', importance: 'high' }
  if (eventType.startsWith('llm_call')) return { stage: 'llm', group: '生成建议', title: eventType.endsWith('started') ? '正在生成建议' : eventType.endsWith('failed') ? '生成建议失败' : '建议生成完成', detail: modelDetail(data), status: eventType.endsWith('failed') ? 'failed' : eventType.endsWith('started') ? 'running' : 'completed', importance: 'high' }
  if (eventType.startsWith('critic')) return { stage: 'critic', group: '质量检查', title: eventType.endsWith('skipped') ? '已跳过完整质量检查' : '质量检查完成', detail: eventType.endsWith('skipped') ? 'AI Chat 使用轻量模式，完整 Critic 留给深度任务。' : '已完成结果校验。', status: eventType.endsWith('skipped') ? 'skipped' : 'completed', importance: 'normal' }
  if (eventType.startsWith('persistence')) return { stage: 'persistence', group: '写入结果', title: eventType.endsWith('started') ? '正在写入结果' : '结果已写入', detail: '消息和运行状态已持久化。', status: eventType.endsWith('started') ? 'running' : 'completed', importance: 'normal' }
  if (eventType.startsWith('memory_write') || eventType === 'memory_written') return { stage: 'memory_write', group: '写入结果', title: eventType.endsWith('skipped') ? '记忆写入后台处理' : '记忆写入完成', detail: eventType.endsWith('skipped') ? '轻量对话不阻塞主回答。' : '关键经验已沉淀到长期记忆。', status: eventType.endsWith('skipped') ? 'skipped' : 'completed', importance: 'low' }
  if (eventType === 'agent_finished') return { stage: 'finished', group: '完成', title: '分析完成', detail: '最终答案已生成。', status: 'completed', importance: 'high' }
  if (eventType.includes('deep')) return { stage: 'deep_agent', group: '生成建议', title: '已切换 DeepAgent', detail: '当前问题需要更深推理，已进入后台深度分析。', status: 'running', importance: 'high' }
  return { stage: rawStage, group: '执行过程', title: event.title || event.message || eventType, detail: event.detail, status: event.status || 'running', importance: 'low' }
}

function intentDetail(data: Record<string, unknown>) {
  const taskPlan = data.task_plan as Record<string, unknown> | undefined
  const intent = taskPlan?.intent as Record<string, unknown> | undefined
  const taskType = data.intent || taskPlan?.primary_task_type || intent?.primary_goal || 'general'
  return `问题类型：${String(taskType)}`
}

function modelDetail(data: Record<string, unknown>) {
  const profile = stringValue(data.model_profile || data.profile) || 'fast'
  const model = stringValue(data.model_name || data.model) || '默认模型'
  return `${profile} / ${model}`
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value : ''
}

async function refreshChatMessage(message: ChatMessage) {
  if (!session.value || !message.id) return
  const latest = await getAiChatMessage(session.value, message.id)
  message.content = latest.content || message.content
  message.status = latest.status || message.status
  message.source = latest.source || message.source
  message.errorMessage = latest.errorMessage
  message.structuredResult = latest.structuredResult || message.structuredResult
  message.taskId = latest.taskId || message.taskId
  message.conversationId = latest.conversationId || message.conversationId
  if (latest.intent) message.intent = latest.intent
  if (['completed', 'failed', 'timeout', 'cancelled'].includes(String(message.status))) {
    stopChatTimelineRecovery(message)
    clearChatBackgroundNotice(message)
    if (activeChatTaskId.value === message.taskId) activeChatTaskId.value = ''
    appendChatProgress(message, String(message.status), String(message.status), message.status === 'completed' ? '完成' : '失败', message.status === 'completed' ? '结果已生成' : (message.errorMessage || '任务未完成'), undefined, undefined, String(message.status), 'high')
    scrollChatToBottom()
  }
}

async function loadAgentRuntimeHealth() {
  if (!session.value) return
  try {
    agentRuntimeHealth.value = await getAgentRuntimeHealth(session.value)
  } catch {
    agentRuntimeHealth.value = null
  }
}

async function restoreLatestChatHistory() {
  if (!session.value) return
  try {
    const conversations = await listAiChatConversations(session.value)
    const latestConversation = conversations[0]
    if (!latestConversation?.id) return
    currentChatConversationId.value = latestConversation.id
    const messages = await fetchAiChatMessages(session.value, latestConversation.id)
    if (!messages.length) return
    chatMessages.value = messages.map((message) => ({ ...message, progressTimeline: [], showRawMarkdown: false }))
    const runningAssistants = chatMessages.value.filter((message) => message.role === 'assistant' && message.taskId && message.status === 'running')
    for (const runningAssistant of runningAssistants) {
      if (runningAssistant.taskId) activeChatTaskId.value = runningAssistant.taskId
      appendChatProgress(runningAssistant, 'history_restored', 'recovery', '恢复进度', '已恢复运行中任务', '已从后端恢复运行中的 Agent 任务。', undefined, 'running', 'high')
      connectChatProgress(runningAssistant)
      startChatTimelineRecovery(runningAssistant)
    }
  } catch {
    chatMessages.value = [{ role: 'assistant', source: 'system', content: '历史对话暂时无法加载，你可以继续发起新的 Agent 对话。' }]
  }
}

function clearChat() {
  currentChatConversationId.value = ''
  activeChatTaskId.value = ''
  stopChatTimelineRecovery()
  clearChatBackgroundNotice()
  chatSocket?.close()
  chatSocketConversationId = ''
  chatMessages.value = [{ role: 'assistant', source: 'system', content: '对话已清空。你可以继续问我经营分析、商品、库存、活动或报告相关问题。' }]
}

function chatSourceLabel(source: string | undefined) {
  if (source === 'agent') return 'Agent 分析'
  if (source === 'workflow') return 'Workflow 分析'
  if (source === 'workflow_fast') return 'Fast Workflow 分析'
  if (source === 'deep_agent') return 'DeepAgent 分析'
  if (source === 'agent_timeout') return 'Agent 超时，已转后台'
  if (source === 'error') return '错误'
  return '系统提示'
}

function truncateAction(value: string | undefined) {
  const text = value || ''
  return text.length > 24 ? `${text.slice(0, 24)}...` : text
}

function escapeHtml(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderInlineMarkdown(value: string) {
  return escapeHtml(value).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
}

function renderMarkdown(markdown: string) {
  const lines = markdown.split(/\r?\n/)
  const html: string[] = []
  let listOpen = false
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      if (listOpen) {
        html.push('</ul>')
        listOpen = false
      }
      continue
    }
    if (trimmed.startsWith('## ')) {
      if (listOpen) html.push('</ul>')
      listOpen = false
      html.push(`<h3>${renderInlineMarkdown(trimmed.slice(3))}</h3>`)
    } else if (trimmed.startsWith('# ')) {
      if (listOpen) html.push('</ul>')
      listOpen = false
      html.push(`<h2>${renderInlineMarkdown(trimmed.slice(2))}</h2>`)
    } else if (/^[-*]\s+/.test(trimmed)) {
      if (!listOpen) {
        html.push('<ul>')
        listOpen = true
      }
      html.push(`<li>${renderInlineMarkdown(trimmed.replace(/^[-*]\s+/, ''))}</li>`)
    } else {
      if (listOpen) html.push('</ul>')
      listOpen = false
      html.push(`<p>${renderInlineMarkdown(trimmed)}</p>`)
    }
  }
  if (listOpen) html.push('</ul>')
  return html.join('')
}

function hasStructuredResult(result: StructuredResult | null | undefined) {
  if (!result) return false
  return Boolean(result.conclusion || result.evidence?.length || result.actions?.length || result.risks?.length || result.missingData?.length || result.stepSummaries?.length)
}

function structuredItems(items: unknown[] | undefined) {
  return (items || []).map((item) => String(item)).filter(Boolean)
}

function stepSummaryTitle(step: NonNullable<StructuredResult['stepSummaries']>[number]) {
  return step.label || step.step || '执行节点'
}

function stepSummaryMeta(step: NonNullable<StructuredResult['stepSummaries']>[number]) {
  const parts = [step.status ? `状态 ${step.status}` : '', typeof step.latencyMs === 'number' ? `${Math.round(step.latencyMs)}ms` : '', Array.isArray(step.rows) ? `${step.rows.length} 行` : ''].filter(Boolean)
  return parts.join(' · ')
}

function logout() {
  clearSession()
  session.value = null
  navigate('/login')
}

function openAgent(agentId: string) {
  selectedAgentId.value = agentId
  navigate(`/agents/${agentId}`)
}

function pageTitle() {
  if (currentPath.value.startsWith('/agents/')) return selectedAgent.value?.name || '数字员工工作台'
  return navItems.find((item) => item.path === currentPath.value)?.label || '工作台'
}

function riskLabel(product: Product) {
  return product.riskLevel === 'high' ? '高风险' : product.riskLevel === 'medium' ? '中风险' : '低风险'
}

onMounted(() => {
  window.addEventListener('ecompilot:auth-expired', () => {
    session.value = null
    navigate('/login')
  })
  window.addEventListener('popstate', () => {
    currentPath.value = window.location.pathname
    if (currentPath.value.startsWith('/agents/')) selectedAgentId.value = currentPath.value.split('/').pop() || ''
    enforceRouteGuard()
  })
  if (currentPath.value.startsWith('/agents/')) selectedAgentId.value = currentPath.value.split('/').pop() || ''
  enforceRouteGuard()
  if (session.value?.user.onboardingCompleted) {
    loadWorkspaceInBackground(session.value, '正在加载工作台数据...')
    loadAgentRuntimeHealth()
    restoreLatestChatHistory()
  }
})

onUnmounted(() => {
  chatSocket?.close()
  stopChatTimelineRecovery()
  clearChatBackgroundNotice()
})
</script>

<template>
  <main v-if="!session" class="auth-page">
    <section class="auth-panel">
      <div class="brand-block">
        <div class="brand-mark">EP</div>
        <p class="eyebrow">EcomPilot</p>
        <h1>电商运营数字员工平台</h1>
        <p>连接多平台店铺数据，让 AI 数字员工自动巡检、发现问题、生成建议和报告。</p>
        <div class="auth-proof"><span>多店铺接入</span><span>工作流式 AI 员工</span><span>报告与策略沉淀</span></div>
      </div>
      <div class="auth-card">
        <div class="segmented"><button :class="{ active: authMode === 'login' }" @click="authMode = 'login'">登录</button><button :class="{ active: authMode === 'register' }" @click="authMode = 'register'">注册</button></div>
        <form v-if="authMode === 'login'" @submit.prevent="submitLogin" class="form-stack">
          <label>手机号或邮箱<input v-model="loginForm.account" autocomplete="username" /></label>
          <label>密码<input v-model="loginForm.password" type="password" autocomplete="current-password" /></label>
          <button class="primary-btn" type="submit" :disabled="isAuthSubmitting">{{ isAuthSubmitting ? '登录中...' : '登录工作台' }}</button>
          <button class="link-btn" type="button">忘记密码</button>
        </form>
        <form v-else @submit.prevent="submitRegister" class="form-stack">
          <label>企业/团队名称<input v-model="registerForm.companyName" required /></label>
          <label>用户姓名<input v-model="registerForm.name" required /></label>
          <label>手机号或邮箱<input v-model="registerForm.email" required autocomplete="username" /></label>
          <label>密码<input v-model="registerForm.password" type="password" required autocomplete="new-password" /></label>
          <label>确认密码<input v-model="registerForm.confirmPassword" type="password" required autocomplete="new-password" /></label>
          <button class="primary-btn" type="submit" :disabled="isAuthSubmitting">{{ isAuthSubmitting ? '创建中...' : '创建团队并初始化' }}</button>
        </form>
        <p v-if="authError" class="error-text">{{ authError }}</p>
      </div>
    </section>
  </main>

  <main v-else-if="!user?.onboardingCompleted" class="onboarding-page">
    <section class="onboarding-shell">
      <aside class="onboarding-rail">
        <div class="brand-mark">EP</div><h1>初始化你的运营工作台</h1><p>先接入店铺、平台和样例数据，再启用数字员工。</p>
        <ol><li :class="{ active: onboardingStep === 1 }">创建店铺</li><li :class="{ active: onboardingStep === 2 }">关联平台</li><li :class="{ active: onboardingStep === 3 }">导入数据</li><li :class="{ active: onboardingStep === 4 }">选择员工</li></ol>
      </aside>
      <section class="onboarding-card">
        <template v-if="onboardingStep === 1">
          <h2>创建店铺</h2>
          <div class="form-grid"><label>店铺名称<input v-model="onboarding.shopName" placeholder="例如：夏日服饰旗舰店" /></label><label>主营类目<input v-model="onboarding.category" /></label><label>店铺类型<select v-model="onboarding.shopType"><option>品牌自营</option><option>代运营</option><option>分销</option><option>其他</option></select></label><label>经营阶段<select v-model="onboarding.businessStage"><option>冷启动</option><option>成长期</option><option>稳定期</option><option>下滑期</option></select></label></div>
        </template>
        <template v-else-if="onboardingStep === 2">
          <h2>关联电商平台</h2>
          <div class="platform-grid"><button v-for="platform in platformOptions" :key="platform" :class="['platform-card', { active: onboarding.selectedPlatforms.includes(platform) }]" @click="toggleOnboardingPlatform(platform)"><strong>{{ platform }}</strong><span>{{ onboarding.selectedPlatforms.includes(platform) ? '已选择' : '待接入' }}</span></button></div>
        </template>
        <template v-else-if="onboardingStep === 3">
          <h2>导入经营数据</h2>
          <div class="choice-grid"><button :class="{ active: onboarding.dataMode === 'sample' }" @click="onboarding.dataMode = 'sample'">使用示例数据体验<span>自动生成订单、商品、活动与库存样例</span></button><button :class="{ active: onboarding.dataMode === 'upload' }" @click="onboarding.dataMode = 'upload'">上传 Excel / CSV<span>解析流程暂以 mock 展示</span></button><button :class="{ active: onboarding.dataMode === 'paste' }" @click="onboarding.dataMode = 'paste'">粘贴数据<span>支持复制表格数据后映射字段</span></button></div>
          <div class="mapping-preview"><span>订单号</span><span>商品 SKU</span><span>成交金额</span><span>库存</span><span>退款状态</span></div>
        </template>
        <template v-else>
          <h2>选择数字员工</h2>
          <div class="agent-select-grid"><button v-for="agent in agentDefinitions" :key="agent.id" :class="{ active: onboarding.enabledAgentIds.includes(agent.id) }" @click="toggleOnboardingAgent(agent.id)"><strong>{{ agent.name }}</strong><span>{{ agent.role }}</span></button></div>
        </template>
        <div class="onboarding-actions"><button class="secondary-btn" :disabled="onboardingStep === 1 || isOnboardingSubmitting" @click="onboardingStep -= 1">上一步</button><button v-if="onboardingStep < 4" class="primary-btn" :disabled="isOnboardingSubmitting" @click="onboardingStep += 1">下一步</button><button v-else class="primary-btn" :disabled="isOnboardingSubmitting" @click="finishOnboarding">{{ isOnboardingSubmitting ? '正在创建...' : '完成并进入工作台' }}</button></div>
      </section>
    </section>
  </main>

  <main v-else class="app-shell">
    <aside class="sidebar"><div class="sidebar-brand"><div class="brand-mark">EP</div><div><strong>EcomPilot</strong><span>电商运营数字员工</span></div></div><nav><button v-for="item in navItems" :key="item.path" :class="{ active: currentPath === item.path || (item.path === '/agents' && currentPath.startsWith('/agents/')) }" @click="navigate(item.path)"><span>{{ item.label }}</span><small>{{ item.hint }}</small></button></nav></aside>
    <section class="workspace">
      <header class="topbar"><div><p class="eyebrow">{{ currentShop?.name }}</p><h1>{{ pageTitle() }}</h1></div><div class="topbar-tools"><label class="shop-picker">当前店铺<select :value="workspace?.currentShopId" @change="switchShop(($event.target as HTMLSelectElement).value)"><option v-for="shop in workspace?.shops" :key="shop.id" :value="shop.id">{{ shop.name }}</option></select></label><span class="status-pill good">{{ authorizedIntegrations.length }} 个平台已接入</span><span class="status-pill">数据更新 {{ currentShop?.lastSyncAt }}</span><span class="status-pill warning">今日待处理 {{ taskCount }}</span><button class="avatar-btn" @click="navigate('/account')">{{ user?.name.slice(0, 1) }}</button></div></header>
      <section class="content-scroll">
        <div v-if="activeJobNotice || activeJobError" class="job-status-bar">
          <span v-if="activeJobNotice" class="success-text">{{ activeJobNotice }}</span>
          <span v-if="activeJobError" class="error-text">{{ activeJobError }}</span>
        </div>
        <div v-if="isWorkspaceRefreshing" class="job-status-bar"><span class="success-text">正在刷新工作台数据...</span></div>
        <template v-if="currentPath === '/dashboard'">
          <div class="metric-grid"><article v-for="card in metricCards" :key="card.label" class="metric-card"><span>{{ card.label }}</span><strong>{{ card.value }}</strong><em>{{ card.trend }}</em></article></div>
          <div class="dashboard-grid">
            <section class="panel wide"><div class="panel-header"><div><p class="eyebrow">Daily Brief</p><h2>昨日经营日报</h2></div><button class="secondary-btn" @click="navigate('/reports')">查看完整报告</button></div><template v-if="hasBusinessData"><p class="summary-text">昨日成交额（GMV）{{ money(workspace!.metrics.gmv) }}，订单 {{ workspace!.metrics.orders }} 单，访客下单比例 {{ workspace!.metrics.conversionRate }}%，退款率 {{ workspace!.metrics.refundRate }}%。</p><div class="insight-row"><span>平均每单金额 {{ money(workspace!.metrics.averageOrderValue) }}</span><span>访客 {{ workspace!.metrics.visitors }}</span><span>库存风险商品编码（SKU） {{ workspace!.metrics.inventoryRiskSkuCount }} 个</span></div><div class="ai-conclusion">AI 结论：已基于当前导入数据生成经营概览，建议继续查看商品、库存和活动详情。</div></template><template v-else><p class="summary-text">当前店铺还没有导入订单、商品、库存或活动数据，经营日报将在数据接入后生成。</p><div class="insight-row"><span>成交额（GMV）0</span><span>订单 0</span><span>访客下单比例 0%</span></div><div class="ai-conclusion">AI 结论：请先在“数据导入”中上传经营数据，或在“平台授权”中完成真实平台授权。</div></template></section>
            <section class="panel inventory-risk-panel"><div class="panel-header"><h2>库存风险巡检</h2><button class="secondary-btn" :disabled="isRunningJob" @click="runReplenishmentPlan">{{ isRunningJob ? '正在分析数据...' : '一键生成补货建议' }}</button></div><p v-if="!topRiskProducts.length" class="summary-text">当前暂无高风险库存。</p><table v-else class="inventory-risk-table"><thead><tr><th>商品编码</th><th>商品名称</th><th>当前库存</th><th>安全库存</th><th>风险等级</th><th>建议动作</th></tr></thead><tbody><tr v-for="product in topRiskProducts" :key="product.id"><td>{{ product.sku }}</td><td :title="product.name">{{ truncateAction(product.name) }}</td><td>{{ product.stock }}</td><td>{{ product.safetyStock ?? 0 }}</td><td><span :class="['risk', product.riskLevel]">{{ riskLabel(product) }}</span></td><td :title="product.suggestedAction || product.aiSuggestion">{{ truncateAction(product.suggestedAction || product.aiSuggestion) }}</td></tr></tbody></table></section>
            <section class="panel"><div class="panel-header"><h2>活动复盘建议</h2><button class="secondary-btn" @click="navigate('/campaigns')">查看复盘报告</button></div><p v-if="!workspace?.campaigns.length" class="summary-text">当前没有可复盘的活动数据。</p><div v-for="campaign in workspace?.campaigns.slice(0, 3)" :key="campaign.id" class="list-item"><strong>{{ campaign.name }}</strong><span>评分 {{ campaign.score }} · 投产比（ROI）{{ campaign.roi }} · 成交额（GMV）{{ money(campaign.gmv) }}</span><p>{{ campaign.conclusion }}</p></div></section>
            <section class="panel wide"><div class="panel-header"><div><p class="eyebrow">Human Review</p><h2>策略进化审核</h2></div></div><p v-if="!pendingStrategies.length" class="summary-text">当前没有待审核策略。</p><table v-else><thead><tr><th>策略</th><th>来源</th><th>预期影响</th><th>风险</th><th>操作</th></tr></thead><tbody><tr v-for="strategy in pendingStrategies" :key="strategy.id"><td>{{ strategy.title }}</td><td>{{ strategy.source }}</td><td>{{ strategy.expectedImpact }}</td><td><span :class="['risk', strategy.riskLevel]">{{ strategy.riskLevel }}</span></td><td class="actions"><button :disabled="updatingStrategyIds.has(strategy.id)" @click="updateStrategy(strategy.id, 'accepted')">接受</button><button :disabled="updatingStrategyIds.has(strategy.id)" @click="updateStrategy(strategy.id, 'deferred')">暂缓</button><button class="danger-btn" :disabled="updatingStrategyIds.has(strategy.id)" @click="updateStrategy(strategy.id, 'rejected')">驳回</button></td></tr></tbody></table></section>
          </div>
        </template>
        <template v-else-if="currentPath === '/agents'"><div class="agent-grid"><article v-for="agent in workspace?.agents" :key="agent.id" class="agent-card"><div class="panel-header"><h2>{{ agent.name }}</h2><span :class="['status-pill', agent.status]">{{ agentStatusText[agent.status] }}</span></div><p>{{ agent.role }}</p><ul><li v-for="item in agent.responsibilities" :key="item">{{ item }}</li></ul><div class="agent-meta"><span>今日任务 {{ agent.tasks.length }}</span><span>最近产出 {{ agent.outputs[0]?.title }}</span></div><button class="primary-btn" @click="openAgent(agent.id)">进入工作台</button></article></div></template>
        <template v-else-if="currentPath.startsWith('/agents/') && selectedAgent"><section class="agent-workbench"><div class="panel hero-panel"><div><p class="eyebrow">Digital Worker</p><h2>{{ selectedAgent.name }}</h2><p>{{ selectedAgent.responsibilities.join(' / ') }}</p></div><div class="hero-actions"><button class="primary-btn" @click="runAgent(selectedAgent)">启动巡检</button><button class="secondary-btn" @click="completeAgentOutput(selectedAgent, `${selectedAgent.name}最新报告`)">生成报告</button></div></div><div v-if="selectedAgent.id === 'store-analyst'" class="two-column"><section class="panel"><h2>数据概览</h2><div class="metric-grid compact"><article v-for="card in metricCards.slice(0, 4)" :key="card.label" class="metric-card"><span>{{ card.label }}</span><strong>{{ card.value }}</strong><em>{{ card.trend }}</em></article></div></section><section class="panel"><h2>异常发现</h2><div class="list-item"><strong>转化率下滑 0.6pt</strong><p>流量增长快于成交增长，主图点击后承接不足。</p></div><div class="list-item"><strong>退款率升高</strong><p>真丝衬衫尺码投诉集中，需要补充尺码建议。</p></div></section><section class="panel wide"><h2>历史日报</h2><table><tbody><tr v-for="report in workspace?.reports.filter((report) => report.type === 'daily')" :key="report.id"><td>{{ report.title }}</td><td>{{ report.summary }}</td><td>{{ report.createdAt }}</td></tr></tbody></table></section></div><div v-else-if="selectedAgent.id === 'product-assistant'" class="two-column"><section class="panel wide"><h2>商品列表与分层</h2><table><thead><tr><th>商品</th><th>分层</th><th>销量</th><th>转化</th><th>优化建议</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.name }}</td><td>{{ product.layer }}</td><td>{{ product.sales }}</td><td>{{ product.conversionRate }}%</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table></section><section class="panel"><h2>问题商品</h2><div v-for="product in workspace?.products.filter((item) => item.layer === '滞销品' || item.riskLevel !== 'low')" :key="product.id" class="list-item"><strong>{{ product.name }}</strong><p>{{ product.riskReason }}</p></div></section></div><div v-else-if="selectedAgent.id === 'inventory-inspector'" class="panel"><h2>风险 SKU 表</h2><table><thead><tr><th>SKU</th><th>商品</th><th>库存</th><th>风险等级</th><th>风险原因</th><th>建议动作</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.sku }}</td><td>{{ product.name }}</td><td>{{ product.stock }}</td><td><span :class="['risk', product.riskLevel]">{{ riskLabel(product) }}</span></td><td>{{ product.riskReason }}</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table><button class="secondary-btn table-action">导出库存风险报告</button></div><div v-else-if="selectedAgent.id === 'campaign-reviewer'" class="panel"><h2>活动复盘工作台</h2><table><thead><tr><th>活动</th><th>评分</th><th>ROI</th><th>GMV</th><th>转化变化</th><th>下次建议</th></tr></thead><tbody><tr v-for="campaign in workspace?.campaigns" :key="campaign.id"><td>{{ campaign.name }}</td><td>{{ campaign.score }}</td><td>{{ campaign.roi }}</td><td>{{ money(campaign.gmv) }}</td><td>{{ campaign.conversionChange }}pt</td><td>{{ campaign.conclusion }}</td></tr></tbody></table></div><div v-else class="two-column"><section class="panel"><h2>报告中心</h2><div v-for="report in workspace?.reports" :key="report.id" class="list-item"><strong>{{ report.title }}</strong><p>{{ report.summary }}</p></div></section><section class="panel"><h2>知识库与历史策略</h2><div v-for="strategy in workspace?.strategies" :key="strategy.id" class="list-item"><strong>{{ strategy.title }}</strong><span>{{ strategy.source }} · {{ strategy.status }}</span></div><div class="button-row"><button class="secondary-btn">生成周报</button><button class="secondary-btn">生成月报</button></div></section></div></section></template>
        <template v-else-if="currentPath === '/reports'"><div class="two-column"><section class="panel"><div class="panel-header"><h2>经营报告中心</h2><button class="primary-btn" :disabled="isRunningJob" @click="runWeeklyReport">{{ isRunningJob ? '正在分析数据...' : '生成管理层周报' }}</button></div><p v-if="!workspace?.reports.length" class="summary-text">暂无经营报告，点击“生成管理层周报”创建第一份报告。</p><div v-for="report in workspace?.reports" :key="report.id" :class="['report-row', { active: selectedReportId === report.id }]" @click="openReportDetail(report.id)"><span>{{ reportTypeLabel(report.type) }}</span><strong>{{ report.title }}</strong><p>{{ report.summary }}</p><em>{{ report.createdAt }} · {{ report.status }}</em><button class="secondary-btn" @click.stop="openReportDetail(report.id)">查看详情</button></div></section><section class="panel report-detail-card"><div class="panel-header"><h2>报告详情</h2><button v-if="selectedReport" class="secondary-btn" @click="showReportMarkdown = !showReportMarkdown">{{ showReportMarkdown ? '隐藏原文' : '查看原文' }}</button></div><template v-if="selectedReport"><div class="report-meta"><span class="status-pill">{{ reportTypeLabel(selectedReport.type) }}</span><span class="status-pill">{{ selectedReport.status }}</span><span>{{ selectedReport.createdAt }}</span></div><h3>{{ selectedReport.title }}</h3><p class="summary-text">{{ selectedReport.summary }}</p><StructuredResultView v-if="hasStructuredResult(selectedReportStructured)" :result="selectedReportStructured" /><article v-if="showReportMarkdown || !hasStructuredResult(selectedReportStructured)" class="markdown-report" v-html="selectedReportHtml"></article></template><p v-else class="summary-text">点击左侧报告查看完整内容。</p></section></div></template>
        <template v-else-if="currentPath === '/products'"><section class="panel"><div class="panel-header"><h2>商品分析</h2><button class="secondary-btn" :disabled="isRunningJob" @click="runProductAnalysis">{{ isRunningJob ? '正在分析数据...' : '生成商品优化方案' }}</button></div><table><thead><tr><th>商品</th><th>商品编码（SKU）</th><th>价格</th><th>库存</th><th>销量</th><th>访客下单比例</th><th>分层</th><th>AI 建议</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.name }}</td><td>{{ product.sku }}</td><td>{{ money(product.price) }}</td><td>{{ product.stock }}</td><td>{{ product.sales }}</td><td>{{ product.conversionRate }}%</td><td>{{ product.layer }}</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table></section></template>
        <template v-else-if="currentPath === '/inventory'"><section class="panel"><div class="panel-header"><h2>库存风险</h2><button class="primary-btn" :disabled="isRunningJob" @click="runReplenishmentPlan">{{ isRunningJob ? '正在分析数据...' : '生成补货建议' }}</button></div><table><thead><tr><th>SKU</th><th>商品</th><th>库存</th><th>风险等级</th><th>风险原因</th><th>建议动作</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.sku }}</td><td>{{ product.name }}</td><td>{{ product.stock }}</td><td><span :class="['risk', product.riskLevel]">{{ riskLabel(product) }}</span></td><td>{{ product.riskReason }}</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table></section></template>
        <template v-else-if="currentPath === '/campaigns'"><section class="panel"><div class="panel-header"><h2>活动复盘</h2><button class="primary-btn" :disabled="isRunningJob" @click="runCampaignReview">{{ isRunningJob ? '正在分析数据...' : '生成复盘报告' }}</button></div><p v-if="!workspace?.campaigns.length" class="summary-text">当前没有可复盘的活动数据，导入活动或订单数据后可生成复盘报告。</p><table v-else><thead><tr><th>活动</th><th>效果评分</th><th>投产比（ROI）</th><th>成交额（GMV）</th><th>访客下单比例变化</th><th>AI 复盘结论</th></tr></thead><tbody><tr v-for="campaign in workspace?.campaigns" :key="campaign.id"><td>{{ campaign.name }}</td><td>{{ campaign.score }}</td><td>{{ campaign.roi }}</td><td>{{ money(campaign.gmv) }}</td><td>{{ campaign.conversionChange }}pt</td><td>{{ campaign.conclusion }}</td></tr></tbody></table></section></template>
        <template v-else-if="currentPath === '/shops'"><div class="two-column"><section class="panel wide"><div class="panel-header"><h2>店铺列表</h2><button class="secondary-btn" @click="editingShopId = null">新建店铺</button></div><table><thead><tr><th>店铺</th><th>平台</th><th>阶段</th><th>授权</th><th>数据导入</th><th>最近同步</th><th>操作</th></tr></thead><tbody><tr v-for="shop in workspace?.shops" :key="shop.id"><td><strong>{{ shop.name }}</strong></td><td>{{ shop.platform }}</td><td>{{ shop.businessStage }}</td><td>{{ shop.status }}</td><td>{{ shop.importStatus }}</td><td>{{ shop.lastSyncAt }}</td><td class="actions"><button @click="switchShop(shop.id)">设为当前</button><button @click="startEditShop(shop)">编辑</button><button class="danger-btn" @click="deleteShop(shop.id)">删除</button></td></tr></tbody></table></section><section class="panel"><h2>{{ editingShopId ? '编辑店铺' : '新建店铺' }}</h2><div class="form-stack"><label>店铺名称<input v-model="shopDraft.name" /></label><label>主营类目<input v-model="shopDraft.category" /></label><label>平台<select v-model="shopDraft.platform"><option v-for="platform in platformOptions" :key="platform">{{ platform }}</option></select></label><label>店铺类型<select v-model="shopDraft.type"><option>品牌自营</option><option>代运营</option><option>分销</option><option>其他</option></select></label><label>经营阶段<select v-model="shopDraft.businessStage"><option>冷启动</option><option>成长期</option><option>稳定期</option><option>下滑期</option></select></label><button class="primary-btn" @click="saveShop">保存店铺</button></div></section></div></template>
        <template v-else-if="currentPath === '/integrations'"><div class="integration-grid"><article v-for="integration in workspace?.integrations" :key="integration.id" class="integration-card"><div class="panel-header"><h2>{{ integration.platform }}</h2><span :class="['status-pill', integration.status]">{{ statusText[integration.status] }}</span></div><p>最近同步：{{ integration.lastSyncAt }}</p><p v-if="integration.errorMessage" class="error-text">{{ integration.errorMessage }}</p><div class="button-row"><button class="primary-btn" @click="setIntegrationStatus(integration, 'authorized')">{{ integration.status === 'authorized' ? '重新授权' : '立即授权' }}</button><button class="secondary-btn" @click="setIntegrationStatus(integration, 'syncing')">开始同步</button><button class="secondary-btn">查看同步日志</button></div></article></div></template>
        <template v-else-if="currentPath === '/data-import'">
          <div class="two-column">
            <section class="panel">
              <h2>导入经营数据</h2>
              <div class="choice-grid vertical">
                <button :class="{ active: selectedImportMode === 'sample' }" @click="selectedImportMode = 'sample'">使用示例数据<span>最快体验完整巡检闭环</span></button>
                <button :class="{ active: selectedImportMode === 'upload' }" @click="selectedImportMode = 'upload'">上传 Excel / CSV<span>订单、商品、库存、活动数据</span></button>
                <button :class="{ active: selectedImportMode === 'paste' }" @click="selectedImportMode = 'paste'">粘贴数据<span>从表格复制后自动识别字段</span></button>
              </div>
              <label v-if="selectedImportMode === 'upload'" class="file-picker">选择 Excel / CSV 文件<input type="file" accept=".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" @change="handleImportFileChange" /></label>
              <label v-if="selectedImportMode === 'paste'" class="paste-editor">粘贴 CSV / TSV 数据<textarea v-model="pasteText" rows="10" placeholder="order_id,customer_id,product_id,product_name,category,unit_price,quantity,pay_amount,stock,safety_stock,visitors,conversions,campaign_name,ad_spend,refund_flag&#10;o001,c001,p001,橙子礼盒,水果生鲜,89,2,178,50,100,1200,80,618活动,300,N"></textarea></label>
              <div class="button-row">
                <button class="primary-btn" :disabled="isImporting" @click="executeImport">{{ isImporting ? '处理中...' : (selectedImportMode === 'sample' ? '执行导入' : '预览字段') }}</button>
                <button v-if="pendingImportJobId" class="secondary-btn" :disabled="isImporting" @click="confirmUploadedImport">确认入库</button>
              </div>
              <p v-if="importNotice" class="success-text">{{ importNotice }}</p>
            </section>
            <section class="panel wide">
              <h2>字段映射与质量检测</h2>
              <div v-if="importPreview" class="mapping-table"><span>源字段</span><span>目标字段</span><span>状态</span><template v-for="field in importPreview.fields" :key="field.sourceField"><span>{{ field.sourceField }}</span><span>{{ field.targetField }}</span><span>{{ Math.round(field.confidence * 100) }}%</span></template></div>
              <div v-else class="mapping-table"><span>源字段</span><span>目标字段</span><span>状态</span><span>order_id</span><span>订单号</span><span>已匹配</span><span>sku_code</span><span>商品 SKU</span><span>已匹配</span><span>pay_amount</span><span>成交金额</span><span>已匹配</span><span>refund_flag</span><span>退款状态</span><span>建议确认</span></div>
              <div v-if="importPreview" class="preview-table"><h2>上传预览</h2><table><thead><tr><th v-for="field in importPreview.fields.slice(0, 6)" :key="field.sourceField">{{ field.sourceField }}</th></tr></thead><tbody><tr v-for="(row, index) in importPreview.rows.slice(0, 5)" :key="index"><td v-for="field in importPreview.fields.slice(0, 6)" :key="field.sourceField">{{ row[field.sourceField] }}</td></tr></tbody></table></div>
              <h2>导入历史</h2>
              <table><thead><tr><th>来源</th><th>文件</th><th>行数</th><th>状态</th><th>质量分</th><th>时间</th></tr></thead><tbody><tr v-for="record in workspace?.imports" :key="record.id"><td>{{ record.source }}</td><td>{{ record.fileName }}</td><td>{{ record.rows }}</td><td>{{ record.status }}</td><td>{{ record.qualityScore }}</td><td>{{ record.createdAt }}</td></tr></tbody></table>
            </section>
          </div>
        </template>
        <template v-else-if="currentPath === '/ai-chat'">
          <section class="ai-chat-page">
            <div class="panel ai-chat-intro">
              <div><p class="eyebrow">Assistant</p><h2>AI 对话助手</h2><p>发送后立即创建 Agent 任务，进度来自后端 WebSocket 与 trace timeline。</p></div>
              <button class="secondary-btn" @click="clearChat">清空对话</button>
            </div>
            <div class="ai-chat-layout">
              <section class="panel chat-panel">
                <div ref="chatMessageListRef" class="chat-message-list">
                  <div v-for="(message, index) in chatMessages" :key="message.id || index" :class="['chat-message', message.role, message.source === 'error' ? 'fallback' : '']">
                    <span class="chat-avatar">{{ message.role === 'assistant' ? 'AI' : user?.name.slice(0, 1) }}</span>
                    <div class="chat-bubble">
                      <div v-if="message.role === 'assistant'" class="chat-source-row"><small class="chat-source">{{ chatSourceLabel(message.source) }}</small><small v-if="message.status" class="chat-status">{{ statusLabel(message.status) }}</small><small v-if="message.intent" class="chat-status">{{ message.intent }}</small><small v-if="message.startedAt" class="chat-status">{{ formatElapsed(message) }}</small></div>
                      <StructuredResultView v-if="message.role === 'assistant' && hasStructuredResult(message.structuredResult)" :result="message.structuredResult" />
                      <p v-else class="chat-answer">{{ message.content }}</p>
                      <div v-if="message.role === 'assistant' && hasStructuredResult(message.structuredResult)" class="raw-report-actions"><button class="secondary-btn" @click="message.showRawMarkdown = !message.showRawMarkdown">{{ message.showRawMarkdown ? '隐藏原文' : '查看原文' }}</button></div>
                      <article v-if="message.showRawMarkdown" class="markdown-report compact" v-html="renderMarkdown(message.content)"></article>
                      <section v-if="message.progressTimeline?.length" class="analysis-process">
                        <button class="analysis-summary" type="button" @click="message.expandedTimeline = !message.expandedTimeline">
                          <span class="timeline-dot active"></span>
                          <span><strong>{{ message.progressTimeline[message.progressTimeline.length - 1]?.title }}</strong><small>{{ message.expandedTimeline ? '收起分析过程' : '展开分析过程' }}</small></span>
                        </button>
                        <div class="chat-timeline compact">
                          <div v-for="item in message.progressTimeline.slice(-3)" :key="`compact-${item.eventType}-${item.stage}-${item.title}`" class="timeline-item">
                            <span class="timeline-dot"></span>
                            <div class="timeline-copy"><span class="timeline-title">{{ item.title }}</span><small v-if="item.detail" class="timeline-meta">{{ item.detail }}</small></div>
                            <small v-if="item.latencyMs" class="timeline-latency">{{ Math.round(item.latencyMs) }}ms</small>
                          </div>
                        </div>
                        <div v-if="message.expandedTimeline" class="chat-timeline full">
                          <div v-for="item in message.progressTimeline" :key="`full-${item.eventType}-${item.stage}-${item.title}`" class="timeline-item">
                            <span class="timeline-dot"></span>
                            <div class="timeline-copy"><span class="timeline-title">{{ item.group }} · {{ item.title }}</span><small v-if="item.detail" class="timeline-meta">{{ item.detail }}</small></div>
                            <small v-if="item.latencyMs" class="timeline-latency">{{ Math.round(item.latencyMs) }}ms</small>
                          </div>
                        </div>
                      </section>
                      <button v-if="message.status === 'failed' || message.status === 'timeout'" class="secondary-btn" @click="chatDraft = message.originalContent || chatMessages[index - 1]?.content || ''; sendChatMessage()">重新执行</button>
                    </div>
                  </div>
                  <div v-if="isChatSending" class="chat-message assistant"><span class="chat-avatar">AI</span><div class="chat-bubble"><div class="chat-source-row"><small class="chat-source">Agent 受理中</small></div><p class="chat-answer">正在创建后台 Agent 任务...</p></div></div>
                </div>
                <form class="chat-composer" @submit.prevent="sendChatMessage"><textarea v-model="chatDraft" placeholder="直接输入问题，例如：这个季节适合卖什么东西？" @keydown.enter.exact.prevent="sendChatMessage"></textarea><button class="primary-btn" type="submit" :disabled="isChatSending || !chatDraft.trim()">{{ isChatSending ? '受理中...' : '发送' }}</button></form>
              </section>
              <aside class="panel chat-suggestions runtime-panel">
                <h2>Agent 执行状态</h2>
                <div class="runtime-card-grid"><div v-for="card in runtimeCards" :key="card.label" :class="['runtime-card', card.tone || 'neutral']"><span>{{ card.label }}</span><strong>{{ card.value }}</strong></div></div>
                <h2>Runtime 健康</h2>
                <div class="runtime-health-row"><span>队列 {{ agentRuntimeHealth?.taskQueue?.status || '未知' }}</span><span>WS {{ agentRuntimeHealth?.monitor?.websocketManager || '未知' }}</span><span>Trace {{ agentRuntimeHealth?.tracer?.backend || '未知' }}</span></div>
                <h2>可直接询问</h2>
                <button @click="chatDraft = '这个季节适合卖什么东西？'; sendChatMessage()">季节选品建议</button><button @click="chatDraft = '哪些库存风险 SKU 需要优先处理？'; sendChatMessage()">库存风险优先级</button><button @click="chatDraft = '能不能推荐我最近爆品'; sendChatMessage()">最近爆品推荐</button><button @click="chatDraft = '哪个商品最值得优化？'; sendChatMessage()">商品优化建议</button>
              </aside>
            </div>
          </section>
        </template>
        <template v-else-if="currentPath === '/account'"><div class="account-grid"><section class="panel account-card"><div class="panel-header"><div><p class="eyebrow">Profile</p><h2>个人资料</h2></div><button class="secondary-btn">编辑资料</button></div><div class="account-kv"><span>姓名</span><strong>{{ user?.name }}</strong><span>邮箱 / 手机</span><strong>{{ user?.email }}</strong><span>角色</span><strong>{{ user?.role }}</strong><span>创建时间</span><strong>{{ user?.createdAt.slice(0, 10) }}</strong></div></section><section class="panel account-card"><div class="panel-header"><div><p class="eyebrow">Company</p><h2>企业资料</h2></div><span class="status-pill good">{{ user?.plan }}</span></div><div class="account-stats"><div><strong>{{ workspace?.shops.length || 0 }}</strong><span>已接入店铺</span></div><div><strong>{{ authorizedIntegrations.length }}</strong><span>已授权平台</span></div></div><div class="account-kv"><span>企业名称</span><strong>{{ user?.companyName }}</strong><span>当前套餐</span><strong>{{ user?.plan }}</strong></div></section><section class="panel account-card wide"><div class="panel-header"><div><p class="eyebrow">Data Access</p><h2>数据与权限</h2></div><span class="status-pill warning">API 开发中 / 未开放</span></div><div class="account-kv three"><span>当前 tenant id</span><strong>{{ user?.defaultTenantId || user?.tenantIds?.[0] || '未绑定' }}</strong><span>当前 shop id</span><strong>{{ workspace?.currentShopId || user?.defaultShopId || '未绑定' }}</strong><span>数据权限</span><strong>订单、商品、库存、活动</strong></div></section><section class="panel account-card"><h2>安全设置</h2><div class="setting-row"><span>修改密码</span><button class="secondary-btn">进入</button></div><div class="setting-row"><span>登录过期说明</span><small>Token 过期后会提示重新登录，不会清理后端数据。</small></div><button class="danger-btn" @click="logout">退出登录</button></section><section class="panel account-card"><h2>通知设置</h2><div class="setting-row"><span>库存风险提醒</span><input type="checkbox" checked /></div><div class="setting-row"><span>日报生成提醒</span><input type="checkbox" checked /></div><div class="setting-row"><span>策略审核提醒</span><input type="checkbox" checked /></div></section></div></template>
      </section>
    </section>
  </main>
</template>