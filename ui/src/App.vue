<script setup lang="ts">
import { ref, onMounted, nextTick, computed } from 'vue'
import axios from 'axios'
import { marked } from 'marked'

// Types
interface Message {
  role: 'user' | 'ai' | 'system'
  content: string
  logs?: LogItem[]
  files?: FileItem[]
  interrupt?: InterruptState
  timestamp?: number
}

interface InterruptState {
  summary: string
  suggestedDecision?: string
}

interface LogItem {
  type: string
  title: string
  details: any
  timestamp: string
}

interface FileItem {
  name: string
  path: string
  url: string
}

interface QuickTask {
  title: string
  desc: string
  prompt: string
}

interface WorkCard {
  title: string
  metric: string
  desc: string
  tasks: QuickTask[]
}

// State
const inputQuery = ref('')
const messages = ref<Message[]>([])
const status = ref<'idle' | 'running'>('idle')
const socket = ref<WebSocket | null>(null)
const hasSessionFiles = ref(false)
const messagesEndRef = ref<HTMLElement | null>(null)
const isWelcomeScreen = computed(() => messages.value.length === 0)
const isSidebarOpen = ref(false)
const fileList = ref<any[]>([])
const resumeInstruction = ref('')
// 生成一个持久的会话ID，如果页面不刷新，ID不变
const currentThreadId = ref(crypto.randomUUID())
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
const authToken = ref(localStorage.getItem('gateway_access_token') ?? '')
const tenantId = ref(localStorage.getItem('gateway_tenant_id') ?? 'tenant_demo')
const shopId = ref(localStorage.getItem('gateway_shop_id') ?? 'default_shop')
const loginUsername = ref(localStorage.getItem('gateway_username') ?? 'local_user')
const loginPassword = ref('admin123')
const loginError = ref('')
const isAuthenticated = computed(() => authToken.value.length > 0)

const authHeaders = () => ({
  Authorization: `Bearer ${authToken.value}`,
  'X-Tenant-ID': tenantId.value,
  'X-Shop-ID': shopId.value
})

const authorizedUrl = (path: string, params: Record<string, string>) => {
  const query = new URLSearchParams({
    ...params,
    token: authToken.value,
    tenant_id: tenantId.value,
    shop_id: shopId.value
  })
  return `${API_BASE_URL}${path}?${query.toString()}`
}

const persistTenantContext = () => {
  localStorage.setItem('gateway_tenant_id', tenantId.value)
  localStorage.setItem('gateway_shop_id', shopId.value)
}

const login = async () => {
  loginError.value = ''
  try {
    const res = await axios.post(`${API_BASE_URL}/api/v1/auth/login`, {
      username: loginUsername.value,
      password: loginPassword.value
    })
    authToken.value = res.data.access_token
    tenantId.value = res.data.user?.default_tenant_id || tenantId.value
    shopId.value = res.data.user?.default_shop_id || shopId.value
    localStorage.setItem('gateway_access_token', authToken.value)
    localStorage.setItem('gateway_tenant_id', tenantId.value)
    localStorage.setItem('gateway_shop_id', shopId.value)
    localStorage.setItem('gateway_username', loginUsername.value)
    connectWebSocket()
  } catch (error: any) {
    loginError.value = error.response?.data?.error?.message || error.message || '登录失败'
  }
}

const logout = () => {
  socket.value?.close()
  socket.value = null
  authToken.value = ''
  localStorage.removeItem('gateway_access_token')
}

const todayBriefs = [
  { label: '昨日经营日报', value: '待生成' },
  { label: '库存风险巡检', value: '待执行' },
  { label: '活动复盘建议', value: '待分析' },
  { label: '策略进化审核', value: '待处理' }
]

const workCards: WorkCard[] = [
  {
    title: '店铺经营分析员',
    metric: 'GMV / 订单 / 转化',
    desc: '巡检销售额、订单量、客单价、转化率和退款率，生成经营诊断。',
    tasks: [
      {
        title: '生成昨日经营日报',
        desc: '销售、订单、商品、库存汇总',
        prompt: '请作为电商运营数字员工，查询昨日店铺经营数据，分析销售额、订单量、客单价、转化率、退款率、爆品、滞销品和库存风险，并生成一份结构完整的昨日经营日报。'
      },
      {
        title: '分析销量异常商品',
        desc: '找出上涨和下滑原因',
        prompt: '请分析最近销量异常波动的商品，分别找出销量上涨最快和下滑最明显的商品，结合订单、库存、流量和活动信息推断原因，并给出运营动作建议。'
      }
    ]
  },
  {
    title: '商品运营助理',
    metric: '爆品 / 滞销 / 库存',
    desc: '识别高潜商品、库存不足、滞销积压和价格异常，输出补货和促销建议。',
    tasks: [
      {
        title: '检查库存风险',
        desc: '低库存和积压商品预警',
        prompt: '请检查当前商品库存风险，识别低于安全库存、库存周转慢、销量下滑但库存较高的商品，并按风险等级给出补货、清仓或活动建议。'
      },
      {
        title: '分析今日爆品',
        desc: '提炼爆品增长因素',
        prompt: '请分析今日或最近表现最好的爆品，说明其销售增长、流量、转化、价格、库存和活动因素，并给出下一步放量建议。'
      }
    ]
  },
  {
    title: '活动复盘专员',
    metric: '投放 / ROI / 复盘',
    desc: '复盘大促、直播、优惠券和广告投放效果，沉淀可复用活动策略。',
    tasks: [
      {
        title: '生成活动复盘报告',
        desc: '评估投入产出和商品表现',
        prompt: '请复盘最近一次电商活动，分析活动期间销售额、订单量、投放成本、ROI、参与商品表现、库存消耗和退款情况，并生成活动复盘报告。'
      },
      {
        title: '分析退款率异常',
        desc: '定位商品和服务问题',
        prompt: '请分析近期退款率异常的商品或订单，定位可能的质量、物流、描述、客服或价格问题，并给出降低退款率的运营建议。'
      }
    ]
  },
  {
    title: '知识与报告专员',
    metric: 'SOP / FAQ / 报告',
    desc: '检索运营 SOP、商品资料和客服知识库，生成报告并发现知识缺口。',
    tasks: [
      {
        title: '发现知识库缺口',
        desc: '沉淀客服和运营 FAQ',
        prompt: '请检查近期电商运营和客服相关问题，结合知识库检索结果，识别知识库缺口、过期规则或冲突口径，并生成一份待补充 FAQ 清单。'
      },
      {
        title: '生成老板汇报材料',
        desc: '面向管理层的简洁结论',
        prompt: '请基于近期店铺经营、商品、库存、活动和客服数据，生成一份面向管理层的电商运营汇报材料，突出关键结论、风险、机会和下一步动作。'
      }
    ]
  }
]

const setQuickTask = (prompt: string) => {
  inputQuery.value = prompt
}

const runQuickTask = async (prompt: string) => {
  inputQuery.value = prompt
  await sendMessage()
}

// Helper: Scroll to bottom
const scrollToBottom = async () => {
  await nextTick()
  if (messagesEndRef.value) {
    messagesEndRef.value.scrollIntoView({ behavior: 'smooth' })
  }
}

// Fetch Files
const fetchFiles = async () => {
  if (!hasSessionFiles.value || !isAuthenticated.value) return
  try {
    const res = await axios.get(`${API_BASE_URL}/api/v1/files`, {
      params: { conversation_id: currentThreadId.value },
      headers: authHeaders()
    })
    if (res.data.files) {
      fileList.value = res.data.files.map((f: any) => ({
        ...f,
        // 下载只传 conversation_id + filename，后端会按当前网关身份校验文件归属。
        url: authorizedUrl('/api/v1/download', { conversation_id: currentThreadId.value, filename: f.filename })
      }))
    }
  } catch (e) {
    console.error('Failed to fetch files', e)
  }
}

// WebSocket Connection
const connectWebSocket = () => {
  if (!isAuthenticated.value) return
  persistTenantContext()
  socket.value?.close()
  const query = new URLSearchParams({
    token: authToken.value,
    tenant_id: tenantId.value,
    shop_id: shopId.value
  })
  const ws = new WebSocket(`${WS_BASE_URL}/api/v1/ws/${currentThreadId.value}?${query.toString()}`)

  ws.onopen = () => {
    console.log('WebSocket Connected')
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      handleSocketMessage(data)
    } catch (e) {
      console.error('Error parsing WS message:', e)
    }
  }

  ws.onclose = () => {
    console.log('WebSocket Disconnected, retrying in 3s...')
    if (isAuthenticated.value) {
      setTimeout(connectWebSocket, 3000)
    }
  }

  socket.value = ws
}

// Handle Incoming Messages
const handleSocketMessage = (data: any) => {
  const { type, event, message, data: eventData } = data

  if (type === 'pong') return

  let lastAiMsg = messages.value.slice().reverse().find(m => m.role === 'ai')
  
  if (event === 'session_created') {
    hasSessionFiles.value = true
    isSidebarOpen.value = true
    fetchFiles()
  } else if (event === 'tool_start') {
    // 触发文件列表刷新，以确保用户能看到生成的文件
    if (hasSessionFiles.value) {
      // 延迟一点刷新，因为工具刚开始运行，文件可能还没生成
      // 但如果是“写入文件”类工具，可能很快就有了
      // 这里可以尝试立即刷新 + 延迟刷新
      fetchFiles()
      setTimeout(fetchFiles, 2000)
    }

    if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'tool',
        title: `使用的工具： ${eventData.tool_name}...`,
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString()
      })
      
      if (eventData.args && eventData.args.filename) {
        if (!lastAiMsg.files) lastAiMsg.files = []
        const fileUrl = authorizedUrl('/api/v1/download', { conversation_id: currentThreadId.value, filename: eventData.args.filename })
        // Avoid duplicates
        if (!lastAiMsg.files.find(f => f.name === eventData.args.filename)) {
           lastAiMsg.files.push({
            name: eventData.args.filename,
            path: eventData.args.filename,
            url: fileUrl
          })
        }
      }
    }
  } else if (event === 'assistant_call') {
    // 同样刷新文件列表
    if (hasSessionFiles.value) {
        fetchFiles()
    }
     if (lastAiMsg) {
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'agent',
        title: `正在使用助手： ${eventData.assistant_name}...`,
        details: eventData.args,
        timestamp: new Date().toLocaleTimeString()
      })
    }
  } else if (event === 'task_result') {
    if (lastAiMsg) {
      lastAiMsg.content = eventData.result
    } else {
       messages.value.push({
        role: 'ai',
        content: eventData.result,
        timestamp: Date.now()
      })
    }
    status.value = 'idle'
    fetchFiles()
  } else if (event === 'human_interrupt') {
    if (lastAiMsg) {
      lastAiMsg.content = '任务检测到可能的重复调用，已暂停等待你的决策。'
      lastAiMsg.interrupt = {
        summary: eventData.summary,
        suggestedDecision: eventData.suggested_decision
      }
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'interrupt',
        title: '已暂停，等待人工决策',
        details: eventData.summary,
        timestamp: new Date().toLocaleTimeString()
      })
    }
    status.value = 'idle'
  } else if (event === 'error') {
    if (lastAiMsg && !lastAiMsg.content) {
      lastAiMsg.content = `任务执行失败：${message}`
      if (!lastAiMsg.logs) lastAiMsg.logs = []
      lastAiMsg.logs.push({
        type: 'error',
        title: '任务执行失败',
        details: message,
        timestamp: new Date().toLocaleTimeString()
      })
    } else {
      messages.value.push({
        role: 'system',
        content: `任务执行失败：${message}`,
        timestamp: Date.now()
      })
    }
    status.value = 'idle'
  }
  
  scrollToBottom()
}

const resumeTask = async (decision: 'continue' | 'revise' | 'abort') => {
  const lastAiMsg = messages.value.slice().reverse().find(m => m.role === 'ai' && m.interrupt)
  if (!lastAiMsg || status.value === 'running' || !isAuthenticated.value) return

  try {
    status.value = 'running'
    await axios.post(`${API_BASE_URL}/api/v1/tasks/${currentThreadId.value}/resume`, {
      decision,
      instruction: resumeInstruction.value
    }, {
      headers: authHeaders()
    })
    if (!lastAiMsg.logs) lastAiMsg.logs = []
    lastAiMsg.logs.push({
      type: 'resume',
      title: `人工决策：${decision}`,
      details: resumeInstruction.value || null,
      timestamp: new Date().toLocaleTimeString()
    })
    lastAiMsg.interrupt = undefined
    resumeInstruction.value = ''
  } catch (error: any) {
    status.value = 'idle'
    messages.value.push({
      role: 'system',
      content: `恢复任务失败：${error.message || error}`,
      timestamp: Date.now()
    })
  }
}

// Send Message
const sendMessage = async () => {
  if ((!inputQuery.value.trim() && selectedFiles.value.length === 0) || status.value === 'running') return
  if (!isAuthenticated.value) {
    messages.value.push({
      role: 'system',
      content: '请先登录网关，再提交任务。',
      timestamp: Date.now()
    })
    return
  }

  const query = inputQuery.value
  inputQuery.value = ''
  status.value = 'running'

  messages.value.push({
    role: 'user',
    content: query,
    timestamp: Date.now()
  })

  messages.value.push({
    role: 'ai',
    content: '', // Start empty, show "Thinking" via logs/status if needed, or placeholder
    logs: [],
    files: [],
    timestamp: Date.now()
  })

  scrollToBottom()

  // Handle File Upload
  if (selectedFiles.value.length > 0) {
    console.log('Uploading files:', selectedFiles.value)
    
    // Log to UI
    const lastAiMsg = messages.value[messages.value.length - 1]
    if (lastAiMsg && lastAiMsg.role === 'ai') {
        if (!lastAiMsg.logs) lastAiMsg.logs = []
        
        const fileDetails = selectedFiles.value.map(f => ({ name: f.name, size: f.size }))
        
        lastAiMsg.logs.push({
            type: 'info',
            title: `Uploading ${selectedFiles.value.length} file(s)...`,
            details: fileDetails,
            timestamp: new Date().toLocaleTimeString()
        })
    }

    // Actual Upload
    try {
        const formData = new FormData()
        // Ensure thread_id is available
        if (typeof currentThreadId !== 'undefined' && currentThreadId.value) {
             formData.append('thread_id', currentThreadId.value)
        } else {
             // Fallback if no thread ID (should ideally not happen as initialized in state)
             console.warn('No thread ID found for upload')
        }

        selectedFiles.value.forEach(file => {
            console.log(`Appending file to FormData: name=${file.name}, size=${file.size}, type=${file.type}`)
            formData.append('files', file)
        })

        await axios.post(`${API_BASE_URL}/api/v1/uploads`, formData, {
            headers: {
            'Content-Type': 'multipart/form-data',
            ...authHeaders()
            }
        })
        
        // Clear files after successful upload
        selectedFiles.value = []
        
        if (lastAiMsg && lastAiMsg.logs) {
            lastAiMsg.logs.push({
                type: 'success',
                title: 'Files uploaded successfully',
                details: null,
                timestamp: new Date().toLocaleTimeString()
            })
        }

    } catch (e: any) {
        console.error('Upload failed', e)
        if (lastAiMsg && lastAiMsg.logs) {
            lastAiMsg.logs.push({
                type: 'error',
                title: 'File upload failed',
                details: e.message || 'Unknown error',
                timestamp: new Date().toLocaleTimeString()
            })
        }
        // Don't stop task execution, but maybe warn user?
    }
  }

  try {
    const payload: any = { query }
    // Only add thread_id if it exists and is not empty
    if (typeof currentThreadId !== 'undefined' && currentThreadId.value) {
      payload.thread_id = currentThreadId.value
    }
    console.log('Sending request payload:', payload)
    const res = await axios.post(`${API_BASE_URL}/api/v1/tasks`, payload, {
      headers: authHeaders()
    })
    
    if (res.data && res.data.thread_id) {
      currentThreadId.value = res.data.thread_id
    }
  } catch (error: any) {
    console.error('Request failed:', error)
    let errorMsg = 'Failed to send request.'
    if (error.message) errorMsg += ` (${error.message})`
    if (error.response && error.response.data) {
        errorMsg += ` Server says: ${JSON.stringify(error.response.data)}`
    }
    
    messages.value.push({
      role: 'system',
      content: errorMsg,
      timestamp: Date.now()
    })
    status.value = 'idle'
  }
}

// File Upload
const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFiles = ref<File[]>([])

const triggerFileUpload = () => {
  fileInputRef.value?.click()
}

const handleFileChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    // Append new files to existing list
    selectedFiles.value = [...selectedFiles.value, ...Array.from(target.files)]
    console.log('Files selected:', selectedFiles.value)
    // Reset input so same file can be selected again if needed
    target.value = ''
  }
}

const removeFile = (index: number) => {
  selectedFiles.value.splice(index, 1)
}

const renderMarkdown = (text: string) => {
  if (!text) return '<span class="typing-indicator">Thinking...</span>'
  return marked(text)
}

onMounted(() => {
  if (isAuthenticated.value) {
    connectWebSocket()
  }
})
</script>

<template>
  <div class="app-container">
    <!-- Main Content -->
    <main class="main-content" :class="{ 'centered-layout': isWelcomeScreen }">
      <section class="auth-strip">
        <div class="auth-fields" v-if="!isAuthenticated">
          <input v-model="loginUsername" placeholder="用户 ID" />
          <input v-model="loginPassword" type="password" placeholder="密码" @keydown.enter="login" />
          <button @click="login">登录网关</button>
          <span v-if="loginError" class="auth-error">{{ loginError }}</span>
        </div>
        <div class="auth-fields" v-else>
          <span class="auth-badge">{{ loginUsername }} / {{ tenantId }} / {{ shopId }}</span>
          <input v-model="tenantId" placeholder="tenant_id" @change="connectWebSocket" />
          <input v-model="shopId" placeholder="shop_id" @change="connectWebSocket" />
          <button @click="logout">退出</button>
        </div>
      </section>
      
      <!-- Sidebar Toggle Button -->
      <button 
        v-if="hasSessionFiles && !isSidebarOpen"
        class="sidebar-toggle-btn" 
        @click="isSidebarOpen = true"
        title="Open File Sidebar"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M4 6H20M4 12H20M4 18H20" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <!-- Welcome Screen -->
      <div v-if="isWelcomeScreen" class="welcome-screen">
        <div class="commerce-hero">
          <div class="hero-copy">
            <span class="eyebrow">E-commerce Digital Worker</span>
            <h1>电商运营数字员工</h1>
            <p>自动巡检店铺数据，分析商品表现，生成经营报告，并把每次运营经验沉淀为可审核的进化策略。</p>
          </div>
          <div class="hero-status">
            <div v-for="item in todayBriefs" :key="item.label" class="status-tile">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </div>
          </div>
        </div>

        <div class="workbench-grid">
          <section v-for="card in workCards" :key="card.title" class="work-card">
            <div class="work-card-header">
              <div>
                <h2>{{ card.title }}</h2>
                <span>{{ card.metric }}</span>
              </div>
            </div>
            <p>{{ card.desc }}</p>
            <div class="quick-task-list">
              <button v-for="task in card.tasks" :key="task.title" class="quick-task" @click="setQuickTask(task.prompt)" @dblclick="runQuickTask(task.prompt)">
                <strong>{{ task.title }}</strong>
                <span>{{ task.desc }}</span>
              </button>
            </div>
          </section>
        </div>
      </div>

      <!-- Chat Area -->
      <div v-else class="chat-scroll-area">
        <div class="chat-container">
          <div v-for="(msg, index) in messages" :key="index" class="message-wrapper" :class="msg.role">
            
            <!-- User Message -->
            <div v-if="msg.role === 'user'" class="message-user">
              <div class="msg-content">{{ msg.content }}</div>
            </div>

            <!-- AI Message -->
            <div v-else-if="msg.role === 'ai'" class="message-ai">
              <div class="ai-avatar">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 2L14.5 9.5L22 12L14.5 14.5L12 22L9.5 14.5L2 12L9.5 9.5L12 2Z" fill="url(#grad1)"/>
                  <defs>
                    <linearGradient id="grad1" x1="2" y1="2" x2="22" y2="22" gradientUnits="userSpaceOnUse">
                      <stop stop-color="#4E75F6"/>
                      <stop offset="1" stop-color="#E3557A"/>
                    </linearGradient>
                  </defs>
                </svg>
              </div>
              
              <div class="ai-content-wrapper">
                <!-- Logs / Thinking Process -->
                <div v-if="msg.logs && msg.logs.length > 0" class="process-section">
                  <details>
                    <summary>
                      <span class="spinner" v-if="status === 'running' && index === messages.length - 1"></span>
                      View thought process
                    </summary>
                    <div class="process-steps">
                      <div v-for="(log, idx) in msg.logs" :key="idx" class="step-item">
                        <div class="step-header">
                          <span class="step-icon">🔧</span>
                          <span class="step-title">{{ log.title }}</span>
                        </div>
                        <div class="step-details" v-if="log.details">
                           <pre>{{ JSON.stringify(log.details, null, 2) }}</pre>
                        </div>
                      </div>
                    </div>
                  </details>
                </div>

                <!-- Text Content -->
                <div class="markdown-body" v-html="renderMarkdown(msg.content)"></div>

                <div v-if="msg.interrupt" class="interrupt-panel">
                  <div class="interrupt-title">需要人工决策</div>
                  <pre>{{ msg.interrupt.summary }}</pre>
                  <textarea
                    v-model="resumeInstruction"
                    placeholder="可选：输入新的执行策略，例如只查数据库并直接给阶段性结论"
                  ></textarea>
                  <div class="interrupt-actions">
                    <button @click="resumeTask('continue')">继续</button>
                    <button @click="resumeTask('revise')">按新策略恢复</button>
                    <button class="danger" @click="resumeTask('abort')">终止</button>
                  </div>
                </div>

                <!-- Files -->
                <div v-if="msg.files && msg.files.length > 0" class="files-grid">
                  <a v-for="file in msg.files" :key="file.name" :href="file.url" target="_blank" class="file-card" :download="file.name">
                    <div class="file-icon">📄</div>
                    <div class="file-info">
                      <div class="file-name">{{ file.name }}</div>
                      <div class="file-type">Document</div>
                    </div>
                  </a>
                </div>
              </div>
            </div>

            <!-- System Message -->
             <div v-else class="message-system">
              {{ msg.content }}
            </div>

          </div>
          <div ref="messagesEndRef" class="spacer-bottom"></div>
        </div>
      </div>

      <!-- Input Area -->
      <footer class="input-footer">
        <!-- File Preview Tab -->
        <div v-if="selectedFiles.length > 0" class="file-preview-container">
          <div v-for="(file, index) in selectedFiles" :key="index" class="file-preview-chip">
            <span class="file-preview-icon">📎</span>
            <span class="file-preview-name">{{ file.name }}</span>
            <button class="file-remove-btn" @click="removeFile(index)" title="Remove file">×</button>
          </div>
        </div>

        <div class="input-container" :class="{ focused: status === 'running' }">
          <input 
            type="file" 
            ref="fileInputRef" 
            multiple
            style="display: none" 
            @change="handleFileChange" 
          />
          <button class="upload-btn" @click="triggerFileUpload" :disabled="status === 'running'" title="Upload file">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
          <textarea 
            v-model="inputQuery" 
            @keydown.enter.exact.prevent="sendMessage"
            placeholder="输入电商运营任务，例如：生成昨日店铺经营日报"
            :disabled="status === 'running'"
          ></textarea>
          <button class="send-btn" @click="sendMessage" :disabled="!isAuthenticated || (!inputQuery.trim() && status !== 'running')">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path>
            </svg>
          </button>
        </div>
        <div class="footer-text">
          电商数字员工会结合数据库、知识库和外部信息生成建议，关键经营决策请结合业务事实复核。
        </div>
      </footer>
    </main>

    <!-- Right Sidebar (File Explorer) -->
    <aside v-if="isSidebarOpen" class="file-sidebar">
      <div class="sidebar-header">
        <h3>Session Files</h3>
        <div style="display: flex; gap: 8px; align-items: center;">
            <button class="folder-btn" @click="fetchFiles" title="Refresh Files" style="padding: 4px 8px;">
                ↻
            </button>
            <button class="close-btn" @click="isSidebarOpen = false">×</button>
        </div>
      </div>
      <div class="file-list">
        <div v-if="fileList.length === 0" class="empty-files">
          No files generated yet.
        </div>
        <div v-else v-for="file in fileList" :key="file.path" class="file-item">
          <a :href="file.url" target="_blank" class="file-link" :download="file.name">
            <span class="file-icon">📄</span>
            <span class="file-name-text">{{ file.name }}</span>
          </a>
        </div>
      </div>
    </aside>
  </div>
</template>

<style>
/* Global Resets & Variables */
:root {
  --bg-dark: #11130f;
  --surface-dark: #1b1f19;
  --surface-light: #293026;
  --text-primary: #f0f4ea;
  --text-secondary: #b9c4b0;
  --accent-blue: #9bd16f;
  --accent-gold: #f0b84b;
  --user-msg-bg: #263021;
  --border-color: #3b4635;
}

body {
  margin: 0;
  background-color: var(--bg-dark);
  color: var(--text-primary);
  font-family: 'Google Sans', 'Roboto', Helvetica, Arial, sans-serif;
  overflow: hidden; /* App handles scroll */
}

/* Layout */
.app-container {
  display: flex;
  height: 100vh;
  width: 100vw;
  /* justify-content: center; Removed to allow sidebar layout */
}

/* Main Content */
.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  position: relative;
  background-color: var(--bg-dark);
  min-width: 0; /* Prevent flex overflow */
}

.auth-strip {
  width: 100%;
  min-height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.75rem 1rem 0;
  box-sizing: border-box;
  z-index: 20;
}

.auth-fields {
  max-width: 920px;
  width: min(920px, calc(100% - 24px));
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.5rem;
  border: 1px solid rgba(155, 209, 111, 0.18);
  border-radius: 10px;
  background: rgba(27, 31, 25, 0.88);
}

.auth-fields input {
  min-width: 150px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: rgba(17, 19, 15, 0.82);
  color: var(--text-primary);
  padding: 0.55rem 0.7rem;
  outline: none;
}

.auth-fields button {
  border: 1px solid rgba(155, 209, 111, 0.38);
  border-radius: 8px;
  background: rgba(155, 209, 111, 0.14);
  color: var(--text-primary);
  padding: 0.55rem 0.85rem;
  cursor: pointer;
}

.auth-badge {
  color: var(--accent-blue);
  font-size: 0.86rem;
  font-weight: 700;
  padding: 0 0.35rem;
}

.auth-error {
  color: #ff8a8a;
  font-size: 0.84rem;
}

.sidebar-toggle-btn {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  z-index: 10;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.sidebar-toggle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
}

/* File Sidebar */
.file-sidebar {
  width: 300px;
  background-color: var(--surface-dark);
  border-left: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-header {
  padding: 1rem;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sidebar-header h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 500;
  color: var(--text-primary);
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.5rem;
  cursor: pointer;
  padding: 0;
  line-height: 1;
}

.close-btn:hover {
  color: var(--text-primary);
}

.file-list {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.empty-files {
  color: var(--text-secondary);
  text-align: center;
  font-size: 0.9rem;
  margin-top: 2rem;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.file-link {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem;
  border-radius: 8px;
  color: var(--text-primary);
  text-decoration: none;
  transition: background 0.2s;
  border: 1px solid transparent;
}

.file-link:hover {
  background: #2D2E30;
  border-color: #444;
}

.folder-btn {
  background: transparent;
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0.5rem;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.folder-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
  border-color: var(--text-secondary);
}

.file-name-text {
  font-size: 0.9rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Welcome Screen */
.welcome-screen {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  align-items: center;
  width: min(1120px, calc(100% - 48px));
  padding: 4rem 0 2rem;
  gap: 1.5rem;
}

/* Centered Layout Mode (Initial State) */
.main-content.centered-layout {
  justify-content: center;
  align-items: center;
  overflow-y: auto;
}

.main-content.centered-layout .welcome-screen {
  flex: 0 0 auto;
  padding-bottom: 2rem;
}

.main-content.centered-layout .input-footer {
  width: 100%;
  max-width: 100%;
  padding: 0;
  background: transparent;
  justify-content: center;
}

.commerce-hero {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr);
  gap: 1rem;
  align-items: stretch;
}

.hero-copy,
.hero-status,
.work-card {
  border: 1px solid rgba(155, 209, 111, 0.18);
  background: linear-gradient(145deg, rgba(32, 39, 28, 0.96), rgba(20, 24, 18, 0.98));
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.22);
}

.hero-copy {
  padding: 2rem;
  border-radius: 14px;
}

.eyebrow {
  display: inline-flex;
  color: var(--accent-gold);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 0.75rem;
}

.hero-copy h1 {
  margin: 0;
  font-size: clamp(2.25rem, 5vw, 4.5rem);
  line-height: 1.05;
  color: var(--text-primary);
}

.hero-copy p {
  max-width: 680px;
  margin: 1rem 0 0;
  color: var(--text-secondary);
  font-size: 1.05rem;
  line-height: 1.75;
}

.hero-status {
  border-radius: 14px;
  padding: 1rem;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.status-tile {
  min-height: 92px;
  border-radius: 10px;
  background: rgba(240, 244, 234, 0.055);
  border: 1px solid rgba(240, 244, 234, 0.08);
  padding: 1rem;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.status-tile span {
  color: var(--text-secondary);
  font-size: 0.86rem;
}

.status-tile strong {
  color: var(--accent-blue);
  font-size: 1.1rem;
}

.workbench-grid {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1rem;
}

.work-card {
  border-radius: 12px;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  min-height: 260px;
}

.work-card-header h2 {
  margin: 0;
  font-size: 1.05rem;
  color: var(--text-primary);
}

.work-card-header span {
  display: inline-flex;
  margin-top: 0.35rem;
  color: var(--accent-gold);
  font-size: 0.78rem;
}

.work-card p {
  margin: 0.85rem 0 1rem;
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.55;
}

.quick-task-list {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  margin-top: auto;
}

.quick-task {
  text-align: left;
  border: 1px solid rgba(240, 244, 234, 0.09);
  border-radius: 9px;
  background: rgba(240, 244, 234, 0.045);
  color: var(--text-primary);
  padding: 0.75rem;
  cursor: pointer;
  transition: border-color 0.18s, background 0.18s, transform 0.18s;
}

.quick-task:hover {
  border-color: rgba(155, 209, 111, 0.45);
  background: rgba(155, 209, 111, 0.09);
  transform: translateY(-1px);
}

.quick-task strong,
.quick-task span {
  display: block;
}

.quick-task strong {
  font-size: 0.9rem;
}

.quick-task span {
  color: var(--text-secondary);
  font-size: 0.78rem;
  margin-top: 0.2rem;
}

/* Chat Area */
.chat-scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.chat-container {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.message-wrapper {
  display: flex;
  flex-direction: column;
  width: 100%;
}

/* User Message */
.message-user {
  align-self: flex-end;
  max-width: 70%;
}

.msg-content {
  background-color: var(--user-msg-bg);
  padding: 12px 18px;
  border-radius: 18px;
  border-bottom-right-radius: 4px;
  line-height: 1.6;
}

/* AI Message */
.message-ai {
  align-self: flex-start;
  width: 100%;
  display: flex;
  gap: 1rem;
}

.ai-avatar {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  margin-top: 4px;
}

.ai-content-wrapper {
  flex: 1;
  min-width: 0; /* Text wrap fix */
}

.markdown-body {
  line-height: 1.6;
  font-size: 1rem;
  overflow-x: auto;
}

.markdown-body pre {
  background: #2D2E30;
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
}

.markdown-body table {
  width: max-content;
  max-width: 100%;
  border-collapse: collapse;
  margin: 1rem 0;
  font-size: 0.9rem;
}

.markdown-body th,
.markdown-body td {
  border: 1px solid var(--border-color);
  padding: 0.55rem 0.7rem;
  vertical-align: top;
  text-align: left;
  white-space: nowrap;
}

.markdown-body th {
  background: rgba(155, 209, 111, 0.12);
  color: var(--text-primary);
  font-weight: 700;
}

.markdown-body td {
  color: var(--text-secondary);
}

.interrupt-panel {
  margin-top: 1rem;
  border: 1px solid rgba(240, 184, 75, 0.35);
  background: rgba(240, 184, 75, 0.08);
  border-radius: 10px;
  padding: 1rem;
}

.interrupt-title {
  color: var(--accent-gold);
  font-weight: 700;
  margin-bottom: 0.75rem;
}

.interrupt-panel pre {
  white-space: pre-wrap;
  color: var(--text-secondary);
  font-size: 0.86rem;
  line-height: 1.5;
  margin: 0 0 0.75rem;
}

.interrupt-panel textarea {
  width: 100%;
  min-height: 72px;
  box-sizing: border-box;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: rgba(17, 19, 15, 0.78);
  color: var(--text-primary);
  padding: 0.75rem;
  resize: vertical;
}

.interrupt-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-top: 0.75rem;
}

.interrupt-actions button {
  border: 1px solid rgba(155, 209, 111, 0.35);
  background: rgba(155, 209, 111, 0.12);
  color: var(--text-primary);
  border-radius: 8px;
  padding: 0.55rem 0.8rem;
  cursor: pointer;
}

.interrupt-actions button.danger {
  border-color: rgba(227, 85, 122, 0.45);
  background: rgba(227, 85, 122, 0.12);
}

.typing-indicator {
  color: var(--text-secondary);
  font-style: italic;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0% { opacity: 0.5; }
  50% { opacity: 1; }
  100% { opacity: 0.5; }
}

/* Process / Logs */
.process-section {
  margin-bottom: 1rem;
}

.process-section summary {
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 0.85rem;
  list-style: none; /* Hide default arrow */
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  border-radius: 4px;
}

.process-section summary:hover {
  background: #2D2E30;
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--text-secondary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.process-steps {
  background: #1E1F20;
  border-radius: 8px;
  padding: 0.5rem;
  margin-top: 0.5rem;
  border: 1px solid #333;
}

.step-item {
  padding: 0.5rem;
  border-left: 2px solid #333;
  margin-left: 0.5rem;
  margin-bottom: 0.5rem;
}

.step-header {
  font-size: 0.85rem;
  font-weight: 500;
  color: #E3E3E3;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.step-details pre {
  margin: 0.5rem 0 0 0;
  font-size: 0.75rem;
  color: #999;
  background: #111;
  padding: 0.5rem;
  border-radius: 4px;
  overflow-x: auto;
}

/* Files Grid */
.files-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1rem;
}

.file-card {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: #2D2E30;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  text-decoration: none;
  color: var(--text-primary);
  border: 1px solid #444;
  transition: all 0.2s;
  min-width: 150px;
}

.file-card:hover {
  background: #333537;
  border-color: #666;
}

.file-info {
  display: flex;
  flex-direction: column;
}

.file-name {
  font-weight: 500;
  font-size: 0.9rem;
}

.file-type {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

/* System Message */
.message-system {
  text-align: center;
  font-size: 0.8rem;
  color: #666;
  margin: 1rem 0;
}

.spacer-bottom { height: 100px; }

/* Input Footer */
.input-footer {
  background: var(--bg-dark); /* Ensure it covers scrolling content */
  padding: 1rem 2rem 2rem 2rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
}

.input-container {
  width: 100%;
  max-width: 800px;
  background: #1E1F20;
  border-radius: 32px;
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  transition: background 0.2s;
}

.input-container.focused {
  background: #2D2E30;
}

textarea {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: 1rem;
  padding: 10px;
  resize: none;
  height: 24px;
  max-height: 200px;
  font-family: inherit;
  outline: none;
}

.send-btn {
  background: none;
  border: none;
  color: var(--text-primary); /* White when active */
  cursor: pointer;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.send-btn:disabled {
  color: #444746;
  cursor: default;
}

.send-btn:not(:disabled):hover {
  background: #3c4043;
}

.upload-btn {
  background: none;
  border: none;
  color: var(--text-primary);
  cursor: pointer;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 4px;
}

.upload-btn:hover {
  background: #3c4043;
}

.upload-btn:disabled {
  color: #444746;
  cursor: default;
}

.footer-text {
  font-size: 0.75rem;
  color: #444746;
  text-align: center;
}

/* File Preview Styles */
.file-preview-container {
  width: 100%;
  max-width: 800px;
  display: flex;
  justify-content: flex-start;
  padding-left: 1rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.file-preview-chip {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: #2D2E30;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #444;
  font-size: 0.9rem;
  color: var(--text-primary);
  animation: slideUp 0.2s ease-out;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.file-preview-icon {
  font-size: 1rem;
}

.file-preview-name {
  max-width: 200px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-remove-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 1.1rem;
  padding: 0 4px;
  line-height: 1;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.file-remove-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ff6b6b;
}

/* Scrollbar Styles */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #444;
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: #555;
}
</style>
