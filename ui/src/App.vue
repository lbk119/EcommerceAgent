<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { agentDefinitions, platformOptions } from './data/mockData'
import { analyzeProducts, completeOnboarding, confirmImportJob, createOrUpdateShop, generateReplenishmentPlan, generateReport, getReport, importSampleData, JobTimeoutError, loadSession, login, logout as clearSession, previewImportJob, refreshWorkspace, register, removeShop, reviewCampaign, saveSession, sendAiMessage, setIntegration, setStrategyStatus, startAgentJob, uploadImportFile, waitForJobAndRefresh } from './services/platformApi'
import type { AgentJob, ImportPreview } from './services/platformApi'
import type { AuthMode, AuthSession, DigitalAgent, Integration, IntegrationStatus, OnboardingPayload, Product, ReportDetail, Shop, StrategyStatus } from './types'

type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

const session = ref<AuthSession | null>(loadSession())
const currentPath = ref(window.location.pathname)
const authMode = ref<AuthMode>('login')
const authError = ref('')
const selectedAgentId = ref('')
const shopDraft = reactive({ name: '', category: '', platform: '淘宝 / 天猫', type: '品牌自营', businessStage: '成长期' })
const editingShopId = ref<string | null>(null)
const selectedImportMode = ref<'sample' | 'upload' | 'paste'>('sample')
const importNotice = ref('')
const selectedImportFile = ref<File | null>(null)
const importPreview = ref<ImportPreview | null>(null)
const pendingImportJobId = ref('')
const isImporting = ref(false)
const activeJobNotice = ref('')
const activeJobError = ref('')
const isRunningJob = ref(false)
const selectedReportId = ref('')
const selectedReport = ref<ReportDetail | null>(null)
const chatDraft = ref('')
const chatMessages = ref<ChatMessage[]>([
  { role: 'assistant', content: '我是 EcomPilot 辅助 AI 助手。你可以直接问经营数据、商品优化、库存风险、活动复盘或报告写作问题。' }
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
const pendingStrategies = computed(() => workspace.value?.strategies.filter((strategy) => strategy.status === 'pending') || [])
const authorizedIntegrations = computed(() => workspace.value?.integrations.filter((item) => item.status === 'authorized' || item.status === 'syncing') || [])
const taskCount = computed(() => workspace.value?.agents.reduce((sum, agent) => sum + agent.tasks.filter((task) => task.status !== '已完成').length, 0) || 0)
const hasBusinessData = computed(() => {
  const metrics = workspace.value?.metrics
  return Boolean(metrics && (metrics.gmv > 0 || metrics.orders > 0 || metrics.visitors > 0 || metrics.activeCampaignProducts > 0 || metrics.inventoryRiskSkuCount > 0))
})
const selectedReportContent = computed(() => selectedReport.value?.contentMarkdown?.trim() || selectedReport.value?.summary || '报告内容生成中，请稍后刷新。')

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
    { label: '昨日 GMV', value: money(metrics.gmv), trend: emptyTrend },
    { label: '订单数', value: metrics.orders.toLocaleString(), trend: emptyTrend },
    { label: '转化率', value: `${metrics.conversionRate}%`, trend: emptyTrend },
    { label: '客单价', value: money(metrics.averageOrderValue), trend: emptyTrend },
    { label: '退款率', value: `${metrics.refundRate}%`, trend: emptyTrend },
    { label: '库存风险 SKU', value: String(metrics.inventoryRiskSkuCount), trend: metrics.inventoryRiskSkuCount > 0 ? `${metrics.inventoryRiskSkuCount} 个风险` : '暂无风险' },
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
  authError.value = ''
  try {
    session.value = await login(loginForm)
    navigate(session.value.user.onboardingCompleted ? '/dashboard' : '/onboarding')
    if (session.value.user.onboardingCompleted) {
      refreshWorkspace(session.value).then((nextSession) => {
        session.value = nextSession
      }).catch((error) => {
        authError.value = error instanceof Error ? error.message : '工作区数据刷新失败'
      })
    }
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '登录失败'
  }
}

async function submitRegister() {
  authError.value = ''
  try {
    session.value = await register(registerForm)
    navigate('/onboarding')
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '注册失败'
  }
}

async function finishOnboarding() {
  if (!session.value) return
  session.value = await completeOnboarding(session.value, onboarding)
  navigate('/dashboard')
  refreshWorkspace(session.value).then((nextSession) => {
    session.value = nextSession
  }).catch(() => {
    // 首次进入工作台不因为完整经营聚合失败而阻塞；用户仍可继续使用基础导航。
  })
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
  session.value = await setStrategyStatus(session.value, strategyId, status)
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
  selectedReport.value = await getReport(session.value, reportId)
}

async function useSampleData() {
  if (!workspace.value || !session.value) return
  const result = await importSampleData(session.value)
  session.value = result.session
  importNotice.value = `示例经营数据已导入，已生成经营概览报告${result.job.generatedReportId ? ' 1 条' : ' 0 条'}、待审核策略 ${result.job.generatedStrategiesCount || 0} 条，可以去工作台查看。`
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
    importNotice.value = '粘贴导入还未开放，请先使用 CSV 上传或示例数据。'
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
    importNotice.value = `数据已确认入库，已生成经营概览报告${result.job.generatedReportId ? ' 1 条' : ' 0 条'}、待审核策略 ${result.job.generatedStrategiesCount || 0} 条，工作台指标已刷新。`
    pendingImportJobId.value = ''
  } catch (error) {
    importNotice.value = error instanceof Error ? error.message : '确认入库失败'
  } finally {
    isImporting.value = false
  }
}

async function sendChatMessage() {
  const content = chatDraft.value.trim()
  if (!content || !session.value) return
  chatMessages.value.push({ role: 'user', content })
  chatDraft.value = ''
  try {
    chatMessages.value.push({ role: 'assistant', content: await sendAiMessage(session.value, content) })
  } catch {
    chatMessages.value.push({ role: 'assistant', content: buildAssistantReply(content) })
  }
}

function clearChat() {
  chatMessages.value = [{ role: 'assistant', content: '对话已清空。你可以继续问我经营分析、商品、库存、活动或报告相关问题。' }]
}

function buildAssistantReply(content: string) {
  const metrics = workspace.value?.metrics
  const highRisk = highRiskProducts.value.map((product) => `${product.name}（${product.riskReason}）`).join('、')
  if (/库存|补货|缺货|滞销/.test(content)) {
    return `当前有 ${metrics?.inventoryRiskSkuCount ?? 0} 个库存风险 SKU，高风险集中在 ${highRisk || '暂无高风险商品'}。建议先处理活动备货不足和低库存商品，再评估滞销清仓策略。`
  }
  if (/日报|经营|GMV|订单|转化/.test(content)) {
    return `昨日 GMV 为 ${money(metrics?.gmv ?? 0)}，订单 ${metrics?.orders ?? 0} 单，转化率 ${metrics?.conversionRate ?? 0}%。主要关注点是增长商品的库存承接，以及退款率 ${metrics?.refundRate ?? 0}% 的异常来源。`
  }
  if (/活动|复盘|ROI|投放/.test(content)) {
    const campaign = workspace.value?.campaigns[0]
    return campaign ? `${campaign.name} 的效果评分为 ${campaign.score}，ROI ${campaign.roi}，GMV ${money(campaign.gmv)}。AI 复盘结论：${campaign.conclusion}` : '当前没有可复盘的活动数据。'
  }
  if (/商品|爆品|优化|价格/.test(content)) {
    const product = workspace.value?.products[0]
    return product ? `${product.name} 当前属于${product.layer}，销量 ${product.sales}，转化率 ${product.conversionRate}%。建议：${product.aiSuggestion}` : '当前没有商品数据。'
  }
  return '我可以基于当前经营数据给出辅助判断。你可以继续追问：库存怎么补、昨日经营异常在哪里、哪个商品应该优化、最近活动复盘结论是什么。'
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
    refreshWorkspace(session.value).then((nextSession) => {
      session.value = nextSession
    }).catch(() => {
      // 保留本地会话，避免后端临时不可用时把用户踢回登录页。
    })
  }
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
          <button class="primary-btn" type="submit">登录工作台</button>
          <button class="link-btn" type="button">忘记密码</button>
        </form>
        <form v-else @submit.prevent="submitRegister" class="form-stack">
          <label>企业/团队名称<input v-model="registerForm.companyName" required /></label>
          <label>用户姓名<input v-model="registerForm.name" required /></label>
          <label>手机号或邮箱<input v-model="registerForm.email" required autocomplete="username" /></label>
          <label>密码<input v-model="registerForm.password" type="password" required autocomplete="new-password" /></label>
          <label>确认密码<input v-model="registerForm.confirmPassword" type="password" required autocomplete="new-password" /></label>
          <button class="primary-btn" type="submit">创建团队并初始化</button>
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
        <div class="onboarding-actions"><button class="secondary-btn" :disabled="onboardingStep === 1" @click="onboardingStep -= 1">上一步</button><button v-if="onboardingStep < 4" class="primary-btn" @click="onboardingStep += 1">下一步</button><button v-else class="primary-btn" @click="finishOnboarding">完成并进入工作台</button></div>
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
        <template v-if="currentPath === '/dashboard'">
          <div class="metric-grid"><article v-for="card in metricCards" :key="card.label" class="metric-card"><span>{{ card.label }}</span><strong>{{ card.value }}</strong><em>{{ card.trend }}</em></article></div>
          <div class="dashboard-grid">
            <section class="panel wide"><div class="panel-header"><div><p class="eyebrow">Daily Brief</p><h2>昨日经营日报</h2></div><button class="secondary-btn" @click="navigate('/reports')">查看完整报告</button></div><template v-if="hasBusinessData"><p class="summary-text">昨日 GMV {{ money(workspace!.metrics.gmv) }}，订单 {{ workspace!.metrics.orders }} 单，转化率 {{ workspace!.metrics.conversionRate }}%，退款率 {{ workspace!.metrics.refundRate }}%。</p><div class="insight-row"><span>客单价 {{ money(workspace!.metrics.averageOrderValue) }}</span><span>访客 {{ workspace!.metrics.visitors }}</span><span>库存风险 {{ workspace!.metrics.inventoryRiskSkuCount }} 个</span></div><div class="ai-conclusion">AI 结论：已基于当前导入数据生成经营概览，建议继续查看商品、库存和活动详情。</div></template><template v-else><p class="summary-text">当前店铺还没有导入订单、商品、库存或活动数据，经营日报将在数据接入后生成。</p><div class="insight-row"><span>GMV 0</span><span>订单 0</span><span>转化率 0%</span></div><div class="ai-conclusion">AI 结论：请先在“数据导入”中上传经营数据，或在“平台授权”中完成真实平台授权。</div></template></section>
            <section class="panel"><div class="panel-header"><h2>库存风险巡检</h2><button class="secondary-btn" :disabled="isRunningJob" @click="runReplenishmentPlan">{{ isRunningJob ? '正在分析数据...' : '一键生成补货建议' }}</button></div><table><thead><tr><th>SKU</th><th>风险</th><th>原因</th><th>建议动作</th></tr></thead><tbody><tr v-for="product in highRiskProducts" :key="product.id"><td>{{ product.sku }}</td><td><span class="risk high">{{ riskLabel(product) }}</span></td><td>{{ product.riskReason }}</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table></section>
            <section class="panel"><div class="panel-header"><h2>活动复盘建议</h2><button class="secondary-btn" @click="navigate('/campaigns')">查看复盘报告</button></div><p v-if="!workspace?.campaigns.length" class="summary-text">当前没有可复盘的活动数据。</p><div v-for="campaign in workspace?.campaigns.slice(0, 3)" :key="campaign.id" class="list-item"><strong>{{ campaign.name }}</strong><span>评分 {{ campaign.score }} · ROI {{ campaign.roi }} · GMV {{ money(campaign.gmv) }}</span><p>{{ campaign.conclusion }}</p></div></section>
            <section class="panel wide"><div class="panel-header"><div><p class="eyebrow">Human Review</p><h2>策略进化审核</h2></div></div><table><thead><tr><th>策略</th><th>来源</th><th>预期影响</th><th>风险</th><th>操作</th></tr></thead><tbody><tr v-for="strategy in pendingStrategies" :key="strategy.id"><td>{{ strategy.title }}</td><td>{{ strategy.source }}</td><td>{{ strategy.expectedImpact }}</td><td><span :class="['risk', strategy.riskLevel]">{{ strategy.riskLevel }}</span></td><td class="actions"><button @click="updateStrategy(strategy.id, 'accepted')">接受</button><button @click="updateStrategy(strategy.id, 'deferred')">暂缓</button><button class="danger-btn" @click="updateStrategy(strategy.id, 'rejected')">驳回</button></td></tr></tbody></table></section>
          </div>
        </template>
        <template v-else-if="currentPath === '/agents'"><div class="agent-grid"><article v-for="agent in workspace?.agents" :key="agent.id" class="agent-card"><div class="panel-header"><h2>{{ agent.name }}</h2><span :class="['status-pill', agent.status]">{{ agentStatusText[agent.status] }}</span></div><p>{{ agent.role }}</p><ul><li v-for="item in agent.responsibilities" :key="item">{{ item }}</li></ul><div class="agent-meta"><span>今日任务 {{ agent.tasks.length }}</span><span>最近产出 {{ agent.outputs[0]?.title }}</span></div><button class="primary-btn" @click="openAgent(agent.id)">进入工作台</button></article></div></template>
        <template v-else-if="currentPath.startsWith('/agents/') && selectedAgent"><section class="agent-workbench"><div class="panel hero-panel"><div><p class="eyebrow">Digital Worker</p><h2>{{ selectedAgent.name }}</h2><p>{{ selectedAgent.responsibilities.join(' / ') }}</p></div><div class="hero-actions"><button class="primary-btn" @click="runAgent(selectedAgent)">启动巡检</button><button class="secondary-btn" @click="completeAgentOutput(selectedAgent, `${selectedAgent.name}最新报告`)">生成报告</button></div></div><div v-if="selectedAgent.id === 'store-analyst'" class="two-column"><section class="panel"><h2>数据概览</h2><div class="metric-grid compact"><article v-for="card in metricCards.slice(0, 4)" :key="card.label" class="metric-card"><span>{{ card.label }}</span><strong>{{ card.value }}</strong><em>{{ card.trend }}</em></article></div></section><section class="panel"><h2>异常发现</h2><div class="list-item"><strong>转化率下滑 0.6pt</strong><p>流量增长快于成交增长，主图点击后承接不足。</p></div><div class="list-item"><strong>退款率升高</strong><p>真丝衬衫尺码投诉集中，需要补充尺码建议。</p></div></section><section class="panel wide"><h2>历史日报</h2><table><tbody><tr v-for="report in workspace?.reports.filter((report) => report.type === 'daily')" :key="report.id"><td>{{ report.title }}</td><td>{{ report.summary }}</td><td>{{ report.createdAt }}</td></tr></tbody></table></section></div><div v-else-if="selectedAgent.id === 'product-assistant'" class="two-column"><section class="panel wide"><h2>商品列表与分层</h2><table><thead><tr><th>商品</th><th>分层</th><th>销量</th><th>转化</th><th>优化建议</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.name }}</td><td>{{ product.layer }}</td><td>{{ product.sales }}</td><td>{{ product.conversionRate }}%</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table></section><section class="panel"><h2>问题商品</h2><div v-for="product in workspace?.products.filter((item) => item.layer === '滞销品' || item.riskLevel !== 'low')" :key="product.id" class="list-item"><strong>{{ product.name }}</strong><p>{{ product.riskReason }}</p></div></section></div><div v-else-if="selectedAgent.id === 'inventory-inspector'" class="panel"><h2>风险 SKU 表</h2><table><thead><tr><th>SKU</th><th>商品</th><th>库存</th><th>风险等级</th><th>风险原因</th><th>建议动作</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.sku }}</td><td>{{ product.name }}</td><td>{{ product.stock }}</td><td><span :class="['risk', product.riskLevel]">{{ riskLabel(product) }}</span></td><td>{{ product.riskReason }}</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table><button class="secondary-btn table-action">导出库存风险报告</button></div><div v-else-if="selectedAgent.id === 'campaign-reviewer'" class="panel"><h2>活动复盘工作台</h2><table><thead><tr><th>活动</th><th>评分</th><th>ROI</th><th>GMV</th><th>转化变化</th><th>下次建议</th></tr></thead><tbody><tr v-for="campaign in workspace?.campaigns" :key="campaign.id"><td>{{ campaign.name }}</td><td>{{ campaign.score }}</td><td>{{ campaign.roi }}</td><td>{{ money(campaign.gmv) }}</td><td>{{ campaign.conversionChange }}pt</td><td>{{ campaign.conclusion }}</td></tr></tbody></table></div><div v-else class="two-column"><section class="panel"><h2>报告中心</h2><div v-for="report in workspace?.reports" :key="report.id" class="list-item"><strong>{{ report.title }}</strong><p>{{ report.summary }}</p></div></section><section class="panel"><h2>知识库与历史策略</h2><div v-for="strategy in workspace?.strategies" :key="strategy.id" class="list-item"><strong>{{ strategy.title }}</strong><span>{{ strategy.source }} · {{ strategy.status }}</span></div><div class="button-row"><button class="secondary-btn">生成周报</button><button class="secondary-btn">生成月报</button></div></section></div></section></template>
        <template v-else-if="currentPath === '/reports'"><div class="two-column"><section class="panel"><div class="panel-header"><h2>经营报告中心</h2><button class="primary-btn" :disabled="isRunningJob" @click="runWeeklyReport">{{ isRunningJob ? '正在分析数据...' : '生成管理层周报' }}</button></div><p v-if="!workspace?.reports.length" class="summary-text">暂无经营报告，点击“生成管理层周报”创建第一份报告。</p><div v-for="report in workspace?.reports" :key="report.id" :class="['report-row', { active: selectedReportId === report.id }]" @click="openReportDetail(report.id)"><span>{{ reportTypeLabel(report.type) }}</span><strong>{{ report.title }}</strong><p>{{ report.summary }}</p><em>{{ report.createdAt }} · {{ report.status }}</em><button class="secondary-btn" @click.stop="openReportDetail(report.id)">查看详情</button></div></section><section class="panel"><h2>报告详情</h2><template v-if="selectedReport"><span class="status-pill">{{ reportTypeLabel(selectedReport.type) }} · {{ selectedReport.status }}</span><h3>{{ selectedReport.title }}</h3><p>{{ selectedReport.summary }}</p><em>{{ selectedReport.createdAt }}</em><pre class="report-detail-text">{{ selectedReportContent }}</pre></template><p v-else class="summary-text">点击左侧报告查看完整内容。</p></section></div></template>
        <template v-else-if="currentPath === '/products'"><section class="panel"><div class="panel-header"><h2>商品分析</h2><button class="secondary-btn" :disabled="isRunningJob" @click="runProductAnalysis">{{ isRunningJob ? '正在分析数据...' : '生成商品优化方案' }}</button></div><table><thead><tr><th>商品</th><th>SKU</th><th>价格</th><th>库存</th><th>销量</th><th>转化率</th><th>分层</th><th>AI 建议</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.name }}</td><td>{{ product.sku }}</td><td>{{ money(product.price) }}</td><td>{{ product.stock }}</td><td>{{ product.sales }}</td><td>{{ product.conversionRate }}%</td><td>{{ product.layer }}</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table></section></template>
        <template v-else-if="currentPath === '/inventory'"><section class="panel"><div class="panel-header"><h2>库存风险</h2><button class="primary-btn" :disabled="isRunningJob" @click="runReplenishmentPlan">{{ isRunningJob ? '正在分析数据...' : '生成补货建议' }}</button></div><table><thead><tr><th>SKU</th><th>商品</th><th>库存</th><th>风险等级</th><th>风险原因</th><th>建议动作</th></tr></thead><tbody><tr v-for="product in workspace?.products" :key="product.id"><td>{{ product.sku }}</td><td>{{ product.name }}</td><td>{{ product.stock }}</td><td><span :class="['risk', product.riskLevel]">{{ riskLabel(product) }}</span></td><td>{{ product.riskReason }}</td><td>{{ product.aiSuggestion }}</td></tr></tbody></table></section></template>
        <template v-else-if="currentPath === '/campaigns'"><section class="panel"><div class="panel-header"><h2>活动复盘</h2><button class="primary-btn" :disabled="isRunningJob" @click="runCampaignReview">{{ isRunningJob ? '正在分析数据...' : '生成复盘报告' }}</button></div><p v-if="!workspace?.campaigns.length" class="summary-text">当前没有可复盘的活动数据，导入活动或订单数据后可生成复盘报告。</p><table v-else><thead><tr><th>活动</th><th>效果评分</th><th>ROI</th><th>GMV</th><th>转化变化</th><th>AI 复盘结论</th></tr></thead><tbody><tr v-for="campaign in workspace?.campaigns" :key="campaign.id"><td>{{ campaign.name }}</td><td>{{ campaign.score }}</td><td>{{ campaign.roi }}</td><td>{{ money(campaign.gmv) }}</td><td>{{ campaign.conversionChange }}pt</td><td>{{ campaign.conclusion }}</td></tr></tbody></table></section></template>
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
              <div class="button-row">
                <button class="primary-btn" :disabled="isImporting" @click="executeImport">{{ isImporting ? '处理中...' : '执行导入' }}</button>
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
        <template v-else-if="currentPath === '/ai-chat'"><section class="ai-chat-page"><div class="panel ai-chat-intro"><div><p class="eyebrow">Assistant</p><h2>AI 对话助手</h2><p>这是辅助入口，用来直接追问经营分析、商品优化、库存风险、活动复盘和报告写作，不替代数字员工工作流。</p></div><button class="secondary-btn" @click="clearChat">清空对话</button></div><div class="ai-chat-layout"><section class="panel chat-panel"><div class="chat-message-list"><div v-for="(message, index) in chatMessages" :key="index" :class="['chat-message', message.role]"><span>{{ message.role === 'assistant' ? 'AI' : user?.name.slice(0, 1) }}</span><p>{{ message.content }}</p></div></div><form class="chat-composer" @submit.prevent="sendChatMessage"><textarea v-model="chatDraft" placeholder="直接输入问题，例如：今天哪些 SKU 需要优先补货？" @keydown.enter.exact.prevent="sendChatMessage"></textarea><button class="primary-btn" type="submit">发送</button></form></section><aside class="panel chat-suggestions"><h2>可直接询问</h2><button @click="chatDraft = '昨日经营日报有哪些异常？'; sendChatMessage()">昨日经营异常</button><button @click="chatDraft = '哪些库存风险 SKU 需要优先处理？'; sendChatMessage()">库存风险优先级</button><button @click="chatDraft = '最近活动复盘结论是什么？'; sendChatMessage()">活动复盘结论</button><button @click="chatDraft = '哪个商品最值得优化？'; sendChatMessage()">商品优化建议</button></aside></div></section></template>
        <template v-else-if="currentPath === '/account'"><div class="two-column"><section class="panel"><h2>个人资料</h2><div class="profile-list"><span>姓名</span><strong>{{ user?.name }}</strong><span>邮箱 / 手机</span><strong>{{ user?.email }}</strong><span>角色</span><strong>{{ user?.role }}</strong><span>创建时间</span><strong>{{ user?.createdAt.slice(0, 10) }}</strong></div></section><section class="panel"><h2>企业资料</h2><div class="profile-list"><span>企业</span><strong>{{ user?.companyName }}</strong><span>当前套餐</span><strong>{{ user?.plan }}</strong><span>API / 数据权限</span><strong>订单、商品、库存、活动只读</strong></div></section><section class="panel"><h2>登录安全</h2><div class="setting-row"><span>密码登录</span><button class="secondary-btn">修改密码</button></div><div class="setting-row"><span>两步验证</span><button class="secondary-btn">开启</button></div></section><section class="panel"><h2>通知设置</h2><div class="setting-row"><span>库存高风险提醒</span><input type="checkbox" checked /></div><div class="setting-row"><span>日报生成通知</span><input type="checkbox" checked /></div><button class="danger-btn" @click="logout">退出登录</button></section></div></template>
      </section>
    </section>
  </main>
</template>