

export const providers = [
  {
    id: 'zhipu',
    name: '智谱 GLM',
    color: '#3b6fe0',
    desc: '智谱 AI GLM 系列大模型',
    apiKey: '',
    enabled: false,
    connected: false,
    models: [
      { id: 'glm-4-plus', name: 'GLM-4-Plus' },
      { id: 'glm-4', name: 'GLM-4' },
      { id: 'glm-4-flash', name: 'GLM-4-Flash' },
      { id: 'glm-4-air', name: 'GLM-4-Air' },
      { id: 'glm-4-long', name: 'GLM-4-Long' }
    ]
  },
  {
    id: 'qwen',
    name: '阿里通义千问',
    color: '#615ced',
    desc: '阿里云通义千问大模型',
    apiKey: '',
    enabled: false,
    connected: false,
    models: [
      { id: 'qwen-max', name: 'Qwen-Max' },
      { id: 'qwen-plus', name: 'Qwen-Plus' },
      { id: 'qwen-turbo', name: 'Qwen-Turbo' },
      { id: 'qwen2.5-72b-instruct', name: 'Qwen2.5-72B-Instruct' }
    ]
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    color: '#4d6bfe',
    desc: '深度求索 DeepSeek 系列',
    apiKey: '',
    enabled: false,
    connected: false,
    models: [
      { id: 'deepseek-chat', name: 'DeepSeek-Chat' },
      { id: 'deepseek-coder', name: 'DeepSeek-Coder' },
      { id: 'deepseek-reasoner', name: 'DeepSeek-Reasoner(R1)' }
    ]
  },
  {
    id: 'moonshot',
    name: '月之暗面 Kimi',
    color: '#1d1d1f',
    desc: 'Moonshot Kimi 长上下文模型',
    apiKey: '',
    enabled: false,
    connected: false,
    models: [
      { id: 'moonshot-v1-8k', name: 'Moonshot-v1-8K' },
      { id: 'moonshot-v1-32k', name: 'Moonshot-v1-32K' },
      { id: 'moonshot-v1-128k', name: 'Moonshot-v1-128K' }
    ]
  },
  {
    id: 'ernie',
    name: '百度文心一言',
    color: '#2932e1',
    desc: '百度 ERNIE 文心大模型',
    apiKey: '',
    enabled: false,
    connected: false,
    models: [
      { id: 'ernie-4.0-8k', name: 'ERNIE-4.0-8K' },
      { id: 'ernie-3.5-8k', name: 'ERNIE-3.5-8K' },
      { id: 'ernie-speed-128k', name: 'ERNIE-Speed-128K' }
    ]
  },
  {
    id: 'spark',
    name: '讯飞星火',
    color: '#e03a2b',
    desc: '科大讯飞星火认知大模型',
    apiKey: '',
    enabled: false,
    connected: false,
    models: [
      { id: 'spark-v4', name: 'Spark-V4' },
      { id: 'spark-v3.5', name: 'Spark-V3.5' },
      { id: 'spark-lite', name: 'Spark-Lite' }
    ]
  },
  {
    id: 'hunyuan',
    name: '腾讯混元',
    color: '#0053e2',
    desc: '腾讯混元大模型',
    apiKey: '',
    enabled: false,
    connected: false,
    models: [
      { id: 'hunyuan-turbos-latest', name: 'Hunyuan-TurboS' },
      { id: 'hunyuan-T1-latest', name: 'Hunyuan-T1' },
      { id: 'hunyuan-pro', name: 'Hunyuan-Pro' },
      { id: 'hunyuan-a13b', name: 'Hunyuan-A13B' },
      { id: 'hunyuan-role-latest', name: 'Hunyuan-Role' }
    ]
  }
]


export const sessions = [
  {
    id: 's1',
    assistantId: 'a1',
    title: '上季度各品类销售额对比',
    group: '今天',
    summary: '按品类汇总了 Q2 销售额并生成柱状图',
    time: '14:32',
    pinned: true,
    messages: [
      { id: 'm1', role: 'user', content: '帮我看看上季度各品类的销售额对比' },
      {
        id: 'm2',
        role: 'assistant',
        content:
          '已为你查询 Q2 各品类销售额，结果如下：\n\n| 品类 | 销售额(万元) |\n|---|---|\n| 数码 | 1280 |\n| 家电 | 960 |\n| 服饰 | 740 |\n| 食品 | 520 |\n\n其中数码品类最高，建议结合去年同期做趋势分析。'
      }
    ]
  },
  {
    id: 's2',
    assistantId: 'a1',
    title: '用户留存率怎么算',
    group: '今天',
    summary: '解释次日/7日留存口径及 SQL 写法',
    time: '11:08',
    pinned: false,
    messages: [
      { id: 'm1', role: 'user', content: '用户留存率怎么算？给个 SQL' },
      {
        id: 'm2',
        role: 'assistant',
        content:
          '次日留存率 = 次日仍活跃用户数 / 当日新增用户数。示例 SQL：\n\n```sql\nSELECT\n  a.dt,\n  COUNT(DISTINCT a.uid) AS new_users,\n  COUNT(DISTINCT b.uid) AS retained\nFROM dws_user_active a\nLEFT JOIN dws_user_active b\n  ON a.uid = b.uid AND b.dt = DATE_ADD(a.dt, 1)\nWHERE a.is_new = 1\nGROUP BY a.dt;\n```'
      }
    ]
  },
  {
    id: 's3',
    assistantId: 'a2',
    title: 'GMV 同比增长情况',
    group: '昨天',
    summary: '查询近 30 天 GMV 并与上月对比',
    time: '昨天 18:20',
    pinned: false,
    messages: [
      { id: 'm1', role: 'user', content: '最近 30 天 GMV 同比上月增长多少？' },
      {
        id: 'm2',
        role: 'assistant',
        content: '近 30 天 GMV 为 4280 万元，上月同期为 3910 万元，同比增长 9.5%。'
      }
    ]
  },
  {
    id: 's4',
    assistantId: 'a3',
    title: '订单表字段说明',
    group: '更早',
    summary: '列出 order 表字段及含义',
    time: '10/05',
    pinned: false,
    messages: [
      { id: 'm1', role: 'user', content: '订单表有哪些字段？' },
      {
        id: 'm2',
        role: 'assistant',
        content: '订单表 dwd_order 含字段：order_id、uid、product_id、amount、status、create_time、pay_time。'
      }
    ]
  }
]


export const assistants = [
  {
    id: 'a1',
    name: '问数助手',
    avatar: '📊',
    color: '#13b5b1',
    tags: ['数据分析', 'SQL'],
    desc: '自然语言提问，自动检索表、写 SQL、解读结果',
    prompt: '你是一个专业的数据分析师助手。请根据用户问题，先检索相关数据表结构，再编写并执行 SQL，最后用中文解读结果。',
    temperature: 0.2,
    maxTokens: 4096,
    topP: 0.9,
    model: 'glm-4-plus',
    knowledgeIds: ['kb1']
  },
  {
    id: 'a2',
    name: 'SQL 审查员',
    avatar: '🔍',
    color: '#615ced',
    tags: ['SQL', '审查'],
    desc: '审查 SQL 正确性、性能与安全',
    prompt: '你是 SQL 审查专家。检查 SQL 的语法、性能（是否全表扫描、索引使用）、安全（是否注入风险），并给出修改建议。',
    temperature: 0.1,
    maxTokens: 2048,
    topP: 0.9,
    model: 'deepseek-coder',
    knowledgeIds: []
  },
  {
    id: 'a3',
    name: '通用助手',
    avatar: '💬',
    color: '#2bb673',
    tags: ['通用'],
    desc: '日常通用对话',
    prompt: '你是一个乐于助人的通用助手，请用简洁清晰的中文回答用户问题。',
    temperature: 0.7,
    maxTokens: 4096,
    topP: 0.95,
    model: 'qwen-max',
    knowledgeIds: []
  }
]


export const knowledgeBases = [
  {
    id: 'kb1',
    name: '数据字典',
    embeddingModel: 'bge-small-zh-v1.5',
    chunkSize: 500,
    overlap: 50,
    docCount: 12,
    updateTime: '2026-10-08',
    docs: [
      { id: 'd1', name: '订单表说明.md', size: '4.2KB', chunks: 18, status: '已索引', preview: '订单表 dwd_order 记录全量订单明细，按天分区...' },
      { id: 'd2', name: '用户表说明.md', size: '3.8KB', chunks: 15, status: '已索引', preview: '用户表 dim_user 记录用户基础信息...' },
      { id: 'd3', name: '商品分类.xlsx', size: '12KB', chunks: 24, status: '已索引', preview: '商品分三级类目，一级类目共 12 个...' },
      { id: 'd4', name: '营销活动.pdf', size: '88KB', chunks: 36, status: '索引中', preview: '2026 年 Q3 营销活动汇总...' }
    ]
  },
  {
    id: 'kb2',
    name: '业务指标口径',
    embeddingModel: 'bge-small-zh-v1.5',
    chunkSize: 500,
    overlap: 50,
    docCount: 5,
    updateTime: '2026-09-30',
    docs: [
      { id: 'd1', name: 'GMV 口径.md', size: '2.1KB', chunks: 8, status: '已索引', preview: 'GMV 定义为已支付订单金额合计...' },
      { id: 'd2', name: '留存口径.md', size: '1.9KB', chunks: 7, status: '已索引', preview: '次日留存指次日仍有活跃行为的用户...' }
    ]
  }
]


export const defaultAssistant = {
  model: 'glm-4-plus',
  assistantModel: 'qwen-max',
  translateModel: 'deepseek-chat'
}
