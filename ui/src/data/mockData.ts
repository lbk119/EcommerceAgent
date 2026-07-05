import type { BusinessMetrics, Campaign, DigitalAgent, ImportRecord, Integration, Product, Report, Shop, Strategy, WorkspaceData } from '../types'

export const platformOptions = ['淘宝 / 天猫', '京东', '拼多多', '抖音电商', '快手电商', '小红书', 'Shopify', '其他平台']

export const agentDefinitions: DigitalAgent[] = [
  {
    id: 'store-analyst',
    name: '店铺经营分析员',
    role: '经营数据巡检与日报生成',
    responsibilities: ['每日经营数据分析', '异常波动识别', '经营日报生成', 'GMV / 订单 / 转化 / 客单价分析'],
    status: 'working',
    tasks: [
      { title: '生成昨日经营日报', status: '进行中', due: '今天 10:00' },
      { title: '识别 GMV 异常波动', status: '已完成', due: '今天 09:20' },
      { title: '同步管理层摘要', status: '待审核', due: '今天 11:30' }
    ],
    outputs: [
      { title: '7 月 4 日经营日报', type: '经营日报', createdAt: '2026-07-05 09:40' },
      { title: '转化率下滑归因摘要', type: '异常分析', createdAt: '2026-07-05 09:18' }
    ]
  },
  {
    id: 'product-assistant',
    name: '商品运营助理',
    role: '商品分层、优化建议与机会识别',
    responsibilities: ['商品表现分析', '爆品 / 潜力品 / 滞销品识别', '标题、价格、主图、库存建议'],
    status: 'idle',
    tasks: [
      { title: '扫描问题商品', status: '待执行', due: '今天 14:00' },
      { title: '输出夏季套装优化方案', status: '已完成', due: '昨天 17:30' }
    ],
    outputs: [
      { title: '商品分层清单 V3', type: '商品方案', createdAt: '2026-07-04 17:30' }
    ]
  },
  {
    id: 'inventory-inspector',
    name: '库存风险巡检员',
    role: '库存不足、滞销与备货风险预警',
    responsibilities: ['库存不足预警', '滞销库存识别', '活动备货风险分析', '周转天数分析'],
    status: 'review',
    tasks: [
      { title: '确认高风险 SKU 补货建议', status: '待审核', due: '今天 12:00' },
      { title: '巡检活动备货风险', status: '已完成', due: '今天 08:50' }
    ],
    outputs: [
      { title: '高风险 SKU 补货清单', type: '库存报告', createdAt: '2026-07-05 08:52' }
    ]
  },
  {
    id: 'campaign-reviewer',
    name: '活动复盘专员',
    role: '活动 ROI、投放效果与复盘沉淀',
    responsibilities: ['活动前后数据对比', 'ROI 分析', '投放效果分析', '活动复盘报告生成'],
    status: 'idle',
    tasks: [
      { title: '复盘 618 返场活动', status: '待执行', due: '今天 15:00' },
      { title: '整理下次直播建议', status: '已完成', due: '昨天 19:00' }
    ],
    outputs: [
      { title: '直播专场复盘摘要', type: '活动复盘', createdAt: '2026-07-04 19:05' }
    ]
  },
  {
    id: 'report-specialist',
    name: '知识与报告专员',
    role: '报告中心、知识库与历史策略维护',
    responsibilities: ['汇总经营报告', '维护运营知识库', '生成周报、月报、复盘文档', '沉淀历史策略'],
    status: 'idle',
    tasks: [
      { title: '生成本周运营周报', status: '待执行', due: '周五 18:00' },
      { title: '沉淀低价引流 SOP', status: '已完成', due: '昨天 16:00' }
    ],
    outputs: [
      { title: '第 27 周运营周报草稿', type: '周报', createdAt: '2026-07-05 10:10' }
    ]
  }
]

export const mockMetrics: BusinessMetrics = {
  date: '2026-07-04',
  gmv: 286420,
  orders: 1842,
  conversionRate: 4.86,
  averageOrderValue: 155.49,
  refundRate: 2.7,
  visitors: 37908,
  inventoryRiskSkuCount: 18,
  activeCampaignProducts: 42,
  aiCompletedTasks: 11
}

export const mockProducts: Product[] = [
  { id: 'p-001', name: '轻氧防晒衣女款', sku: 'SUN-COAT-001', category: '服饰', price: 189, stock: 46, sales: 1280, conversionRate: 7.8, riskLevel: 'high', layer: '爆品', riskReason: '活动备货不足', aiSuggestion: '建议今日补货 900 件，并将直播间库存锁定比例提高到 35%。' },
  { id: 'p-002', name: '冰感阔腿裤', sku: 'PANTS-ICE-021', category: '服饰', price: 129, stock: 1680, sales: 215, conversionRate: 2.1, riskLevel: 'medium', layer: '滞销品', riskReason: '周转过慢', aiSuggestion: '建议设置 3 件 8 折组合，并替换首图突出冰感面料。' },
  { id: 'p-003', name: '通勤托特包', sku: 'BAG-TOTE-117', category: '箱包', price: 239, stock: 320, sales: 486, conversionRate: 5.4, riskLevel: 'low', layer: '潜力品', riskReason: '稳定增长', aiSuggestion: '建议加入会员日活动，测试 219 元券后价。' },
  { id: 'p-004', name: '夏季真丝衬衫', sku: 'SHIRT-SILK-033', category: '服饰', price: 299, stock: 24, sales: 312, conversionRate: 6.3, riskLevel: 'high', layer: '稳态品', riskReason: '库存不足', aiSuggestion: '建议暂停低 ROI 投放，优先保障自然流量成交。' },
  { id: 'p-005', name: '儿童速干短袖', sku: 'KIDS-TEE-090', category: '童装', price: 79, stock: 820, sales: 940, conversionRate: 8.1, riskLevel: 'medium', layer: '爆品', riskReason: '尺码结构失衡', aiSuggestion: '建议补齐 120/130 码，并在详情页增加尺码推荐。' }
]

export const mockCampaigns: Campaign[] = [
  { id: 'c-001', name: '618 返场直播专场', score: 86, roi: 3.8, gmv: 842000, conversionChange: 1.2, conclusion: '直播间爆品承接好，但尾款后退款率升高，需要提前筛掉低毛利 SKU。' },
  { id: 'c-002', name: '会员日满减活动', score: 73, roi: 2.4, gmv: 312000, conversionChange: 0.4, conclusion: '会员复购贡献稳定，建议增加高客单组合装提升利润。' },
  { id: 'c-003', name: '夏装清仓专场', score: 68, roi: 1.9, gmv: 196000, conversionChange: -0.3, conclusion: '低价拉新有效，但主图与券后价表达不清导致转化不足。' }
]

export const mockStrategies: Strategy[] = [
  { id: 's-001', title: '将防晒衣直播库存阈值从 600 提高到 1100', source: '库存风险巡检员', expectedImpact: '预计减少 18% 断货损失，GMV 增量约 5.6 万', riskLevel: 'medium', status: 'pending', createdAt: '2026-07-05 09:20' },
  { id: 's-002', title: '对冰感阔腿裤开启组合装清仓策略', source: '商品运营助理', expectedImpact: '预计 14 天释放 36% 滞销库存', riskLevel: 'low', status: 'pending', createdAt: '2026-07-05 08:45' },
  { id: 's-003', title: '下次会员日减少低毛利 SKU 投放预算', source: '活动复盘专员', expectedImpact: '预计 ROI 提升 0.4-0.7', riskLevel: 'medium', status: 'deferred', createdAt: '2026-07-04 20:10' }
]

export const mockReports: Report[] = [
  { id: 'r-001', type: '经营日报', title: '7 月 4 日店铺经营日报', summary: 'GMV 环比提升 12.4%，防晒衣贡献主要增量；退款率略升，需关注真丝衬衫尺码投诉。', createdAt: '2026-07-05 09:42', status: 'ready' },
  { id: 'r-002', type: '库存风险', title: '高风险 SKU 巡检报告', summary: '18 个 SKU 进入风险池，其中 4 个为高风险，主要集中在直播备货与尺码结构。', createdAt: '2026-07-05 08:52', status: 'ready' },
  { id: 'r-003', type: '活动复盘', title: '618 返场直播专场复盘', summary: '活动 ROI 达 3.8，投放结构健康；建议下次提前设置库存保护线。', createdAt: '2026-07-04 19:05', status: 'ready' }
]

export const mockImports: ImportRecord[] = [
  { id: 'i-001', source: '示例数据', fileName: 'ecompilot_sample_july.csv', rows: 2480, status: '已导入', createdAt: '2026-07-05 09:00', qualityScore: 96 },
  { id: 'i-002', source: 'Excel 上传', fileName: 'campaign_orders.xlsx', rows: 682, status: '字段已映射', createdAt: '2026-07-04 16:30', qualityScore: 89 }
]

export const createMockWorkspace = (overrides?: Partial<Shop>): WorkspaceData => {
  const shop: Shop = {
    id: 'shop-main',
    name: overrides?.name || '示例旗舰店',
    category: overrides?.category || '服饰鞋包',
    platform: overrides?.platform || '淘宝 / 天猫',
    status: 'active',
    type: overrides?.type || '品牌自营',
    businessStage: overrides?.businessStage || '成长期',
    lastSyncAt: '2026-07-05 09:12',
    importStatus: '示例数据已导入'
  }

  const shops: Shop[] = [
    shop,
    { id: 'shop-live', name: '抖音直播店', category: '服饰鞋包', platform: '抖音电商', status: 'active', type: '品牌自营', businessStage: '成长期', lastSyncAt: '2026-07-05 08:58', importStatus: 'API 同步中' },
    { id: 'shop-distribution', name: '分销渠道店', category: '箱包配饰', platform: '京东', status: 'setup', type: '分销', businessStage: '冷启动', lastSyncAt: '未同步', importStatus: '待导入' }
  ]

  const integrations: Integration[] = [
    { id: 'int-tmall', platform: '淘宝 / 天猫', status: 'authorized', lastSyncAt: '2026-07-05 09:12' },
    { id: 'int-jd', platform: '京东', status: 'expired', lastSyncAt: '2026-07-02 18:10', errorMessage: '授权已过期，请重新授权' },
    { id: 'int-pdd', platform: '拼多多', status: 'unauthorized', lastSyncAt: '未同步' },
    { id: 'int-douyin', platform: '抖音电商', status: 'syncing', lastSyncAt: '2026-07-05 09:05' },
    { id: 'int-kuaishou', platform: '快手电商', status: 'failed', lastSyncAt: '2026-07-04 22:40', errorMessage: '订单接口限流，等待重试' },
    { id: 'int-red', platform: '小红书', status: 'unauthorized', lastSyncAt: '未同步' },
    { id: 'int-shopify', platform: 'Shopify', status: 'authorized', lastSyncAt: '2026-07-05 07:30' }
  ]

  return {
    currentShopId: shop.id,
    shops,
    integrations,
    metrics: mockMetrics,
    products: mockProducts,
    agents: agentDefinitions,
    reports: mockReports,
    strategies: mockStrategies,
    campaigns: mockCampaigns,
    imports: mockImports
  }
}