import { createMockWorkspace } from '../data/mockData'
import type { AuthSession, LoginPayload, OnboardingPayload, RegisterPayload, User } from '../types'

const STORAGE_KEY = 'ecompilot_session'

const createToken = () => `mock_${crypto.randomUUID()}`

const persist = (session: AuthSession) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
}

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

export const saveSession = (session: AuthSession) => persist(session)

export const loginWithMock = async (payload: LoginPayload): Promise<AuthSession> => {
  const stored = loadSession()
  if (stored) {
    return { ...stored, token: stored.token || createToken() }
  }

  const fallbackUser: User = {
    id: crypto.randomUUID(),
    name: payload.account.includes('@') ? '运营负责人' : payload.account,
    email: payload.account.includes('@') ? payload.account : 'operator@example.com',
    companyName: 'EcomPilot 示例团队',
    role: '管理员',
    createdAt: new Date().toISOString(),
    onboardingCompleted: false,
    plan: '试用版'
  }
  const session: AuthSession = { token: createToken(), user: fallbackUser, workspace: createMockWorkspace() }
  persist(session)
  return session
}

export const registerWithMock = async (payload: RegisterPayload): Promise<AuthSession> => {
  if (payload.password !== payload.confirmPassword) {
    throw new Error('两次输入的密码不一致')
  }
  const user: User = {
    id: crypto.randomUUID(),
    name: payload.name,
    email: payload.email,
    companyName: payload.companyName,
    role: '管理员',
    createdAt: new Date().toISOString(),
    onboardingCompleted: false,
    plan: '试用版'
  }
  const session: AuthSession = { token: createToken(), user, workspace: createMockWorkspace({ name: `${payload.companyName}示例店` }) }
  persist(session)
  return session
}

export const completeOnboarding = async (session: AuthSession, payload: OnboardingPayload): Promise<AuthSession> => {
  const workspace = createMockWorkspace({
    name: payload.shopName,
    category: payload.category,
    platform: payload.selectedPlatforms[0] || '淘宝 / 天猫',
    type: payload.shopType,
    businessStage: payload.businessStage
  })
  workspace.integrations = workspace.integrations.map((integration) => ({
    ...integration,
    status: payload.selectedPlatforms.includes(integration.platform) ? 'authorized' : integration.status
  }))
  workspace.agents = workspace.agents.filter((agent) => payload.enabledAgentIds.includes(agent.id))
  const nextSession: AuthSession = {
    ...session,
    user: { ...session.user, onboardingCompleted: true },
    workspace
  }
  persist(nextSession)
  return nextSession
}

export const logoutMock = () => {
  localStorage.removeItem(STORAGE_KEY)
}