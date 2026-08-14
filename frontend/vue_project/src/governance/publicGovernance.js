export const GOVERNANCE_CONTACT = Object.freeze({
  email: 'contact@globemind.top',
  label: 'GlobeMind 共享受理邮箱',
  note: '当前尚未公布具名负责人和响应 SLA；该邮箱仅作为统一受理与转交入口。',
})

export const GOVERNANCE_REVISION = Object.freeze({
  version: 'V0.9',
  updatedAt: '2026-08-09',
  note: '首次发布公开治理入口；待责任人任命、法务复核与变更记录机制完成后再更新。',
})

export const RESPONSIBILITY_MATRIX = Object.freeze([
  Object.freeze({
    area: '账号与个人信息',
    role: '隐私与身份管理责任角色',
    owner: '待指定',
    intake: '/corrections 中的「隐私与账号」入口',
  }),
  Object.freeze({
    area: '服务可用性与安全漏洞',
    role: '服务运营与安全责任角色',
    owner: '待指定',
    intake: '/security 与 /corrections 的安全入口',
  }),
  Object.freeze({
    area: '数据集、来源与许可',
    role: '数据治理责任角色',
    owner: '待指定',
    intake: '/corrections 中的「数据与来源」入口',
  }),
  Object.freeze({
    area: '模型、指数与分析结论',
    role: '模型治理与研究质量责任角色',
    owner: '待指定',
    intake: '/corrections 中的「分析与模型」入口',
  }),
])

export const PUBLIC_GOVERNANCE_PAGES = Object.freeze({
  '/privacy': Object.freeze({
    eyebrow: 'PRIVACY / V0.9',
    title: '隐私说明',
    status: '临时透明说明 · 待隐私负责人与法务复核',
    summary:
      '本页先公开当前可以确认的注册信息与处理边界。它不冒充已完成的个人信息清单、保留制度或法律意见。',
    sections: Object.freeze([
      Object.freeze({
        title: '当前注册所需信息',
        paragraphs: Object.freeze([
          '现有注册接口仅要求用户名、邮箱和密码。姓名与手机号已改为可选资料，可留空并可在个人中心清除；邮箱用于账号识别与密码找回。',
          '密码用于身份验证，请不要在纠错邮件、截图或运维记录中提交密码、重置 token 或其他凭据。',
        ]),
      }),
      Object.freeze({
        title: '目的与使用边界',
        paragraphs: Object.freeze([
          '已确认的目的包括建立账号、登录验证、账号唯一性校验和密码重置。运行期间还可能产生安全与故障排查记录。',
          '完整的处理活动、第三方处理者、跨境情况与模型训练用途盘点尚未公布。在该盘点完成前，请不要上传敏感个人信息或需要特殊保护的材料。',
        ]),
      }),
      Object.freeze({
        title: '保留、权利与删除',
        paragraphs: Object.freeze([
          '个人中心现已提供资料更正、当前已接入数据的 JSON 导出，以及可撤销的账号删除申请登记。导出会明确列出尚未接入的工作区、定时任务和研究项目范围；删除申请不会冒充已经执行。经批准的分类保留期、跨子系统擦除和完整注销执行仍是待整改项。',
          '如需提出账号或个人信息请求，请使用纠错入口并选择「隐私与账号」。请仅提供完成身份核验所需的最少信息。',
        ]),
      }),
    ]),
    related: Object.freeze(['/terms', '/security', '/methodology', '/sources', '/corrections']),
  }),
  '/terms': Object.freeze({
    eyebrow: 'TERMS / V0.9',
    title: '服务条款与使用边界',
    status: '公开测试使用说明 · 待运营负责人与法务复核',
    summary:
      'GlobeMind 当前是研究辅助与算法实验性产品。本页明确公开测试的最低使用边界，不冒充已完成审批的商业服务协议。',
    sections: Object.freeze([
      Object.freeze({
        title: '产品定位',
        paragraphs: Object.freeze([
          '系统输出用于线索发现和辅助浏览，不应被直接当作实时预警、风险评级、政策结论、投资/安全建议或可追责的正式报告。',
          '数据状态、截止时间、覆盖率、来源和模型版本应与结果一起核验。缺失这些信息时，不应根据精确分数作出决策。',
        ]),
      }),
      Object.freeze({
        title: '账号与允许的使用',
        paragraphs: Object.freeze([
          '用户应保护账号凭据，不得利用服务进行未授权访问、破坏性测试、绕过访问控制或侵犯他人权利的活动。',
          '请对下载、引用和二次传播的来源内容自行核验权利与许可。平台的全量来源许可登记尚未完成，因此页面不授予超出原来源条款的再利用权。',
        ]),
      }),
      Object.freeze({
        title: '可用性与变更',
        paragraphs: Object.freeze([
          '当前未对公开测试承诺可用性或响应 SLA。运行异常、数据降级和功能调整应在界面中显示；未显示不等于服务正常。',
          '重大条款变更应更新本页版本并重新告知。版本化和变更记录仍是待完成项。',
        ]),
      }),
    ]),
    related: Object.freeze(['/privacy', '/security', '/methodology', '/sources', '/corrections']),
  }),
  '/security': Object.freeze({
    eyebrow: 'SECURITY / V0.9',
    title: '安全报告与披露入口',
    status: '临时公开入口 · 安全负责人和响应 SLA 待指定',
    summary:
      '本页提供一个可发现的安全报告通道。它不是安全认证、渗透测试结论、漏洞赏金承诺或事故响应 SLA。',
    sections: Object.freeze([
      Object.freeze({
        title: '如何报告',
        paragraphs: Object.freeze([
          '请使用共享受理邮箱，主题以 [SECURITY] 开头，包含受影响路径或组件、可重现的最少步骤、影响和安全的联系方式。',
          '不要在邮件中发送密码、token、API 密钥、数据库连接串或无关个人信息。如证据含敏感内容，请先发送不含秘密的摘要，等待安全传输方式。',
        ]),
      }),
      Object.freeze({
        title: '安全测试边界',
        paragraphs: Object.freeze([
          '未经明确授权，请不要执行会破坏数据、影响可用性、获取他人数据或扩大访问的测试。',
          '当前尚未公布 MFA、SSO、会话管理、数据驻留、加密、备份和删除的完整可验证说明；机构采用前应单独完成安全评估。',
        ]),
      }),
    ]),
    related: Object.freeze(['/privacy', '/terms', '/methodology', '/sources', '/corrections']),
  }),
  '/methodology': Object.freeze({
    eyebrow: 'METHODOLOGY / V0.9',
    title: '方法说明与解读边界',
    status: '公开方法概览 · 完整指标清单、验证记录与责任人待补齐',
    summary:
      '本页说明当前产品输出应如何解读以及已知限制。它不是完整可复现实验记录、独立有效性证明、算法认证或模型性能承诺。',
    sections: Object.freeze([
      Object.freeze({
        title: '当前可确认的方法层级',
        paragraphs: Object.freeze([
          '产品界面包含多语言新闻检索、事件聚合与走势组织、来源对比，以及舆情和数值分析等能力。聚类、标签、摘要、排序、分数和研判文字均可能是派生结果，应回到原始来源、记录时间与当前筛选条件核验。',
          '不同页面可能组合规则、检索、统计方法或机器学习模型。模型名称、训练数据、版本、参数、阈值和适用范围的全量公开清单尚未完成；信息缺失时，不应假设不同页面的方法相同或结果可复现。',
        ]),
      }),
      Object.freeze({
        title: '评分、排序与不确定性',
        paragraphs: Object.freeze([
          '界面中的精确分数、排名、风险级别或趋势标签不自动等同于经校准概率、事实结论或行动建议。显示更多小数位也不代表更高置信度。',
          '来源覆盖、采集延迟、重复记录、翻译、去重、缺失字段、异常值与模型漂移都可能改变结果。重要判断应使用多个独立来源交叉验证，并保留人工复核。',
        ]),
      }),
      Object.freeze({
        title: '验证、变更与纠错',
        paragraphs: Object.freeze([
          '当前尚未公布覆盖全部功能的基准集、验收阈值、误差分层、漂移监测和版本回滚记录，也尚未指定具名模型治理负责人。这些缺口会在形成可验证材料后更新。',
          '如发现可复现的方法、评分或分析问题，请通过纠错入口提交稳定链接、记录 ID、时间、当前筛选条件和核验依据；共享受理入口不代表已承诺响应 SLA。',
        ]),
      }),
    ]),
    related: Object.freeze(['/privacy', '/terms', '/security', '/sources', '/corrections']),
  }),
  '/sources': Object.freeze({
    eyebrow: 'SOURCES / V0.9',
    title: '数据来源与许可说明',
    status: '公开来源边界 · 全量目录、覆盖率、许可登记与责任人待补齐',
    summary:
      '本页说明来源信息的最低核验边界。平台收录或展示记录不表示平台拥有原内容，也不授予超出原来源条款的访问、复制或再利用许可。',
    sections: Object.freeze([
      Object.freeze({
        title: '界面可见的来源信息',
        paragraphs: Object.freeze([
          '在底层记录具备相应字段时，界面会展示来源名称或域名、原文链接、发布时间与语言等信息；历史记录可能缺项，来源标签也可能需要进一步规范化。',
          '聚合、翻译、摘要或事件关联不会转移原内容权利。引用、下载和二次传播前，应访问原始链接并核验原发布者的署名、访问条件与许可。',
        ]),
      }),
      Object.freeze({
        title: '覆盖与时效限制',
        paragraphs: Object.freeze([
          '当前尚未公布可核验的全量来源目录、地区与语言覆盖率、时间跨度、采集方式、更新延迟、保留期或来源级可靠性评价。没有出现在结果中的内容不能据此推断为不存在。',
          '采集延迟、页面变更、访问限制、重复与删除都可能造成缺失或过时。使用结果时应同时检查原文时间、平台记录时间、筛选条件和数据新鲜度提示。',
        ]),
      }),
      Object.freeze({
        title: '许可、来源纠错与移除请求',
        paragraphs: Object.freeze([
          '全量许可与溯源登记尚未完成，因此平台不提供覆盖全部数据的统一开放许可证或再分发授权；具体权利以原来源条款和适用规则为准。',
          '如需纠正来源归属、失效链接、元数据或提出权利/移除请求，请通过纠错入口提供页面路径、记录 ID、原始来源和可核验依据。当前共享邮箱仅负责受理与转交，具名负责人和响应 SLA 待指定。',
        ]),
      }),
    ]),
    related: Object.freeze(['/privacy', '/terms', '/security', '/methodology', '/corrections']),
  }),
  '/corrections': Object.freeze({
    eyebrow: 'CORRECTIONS / V0.9',
    title: '纠错与权利请求',
    status: '统一受理入口 · 跟踪编号、公开修订日志与响应 SLA 待上线',
    summary:
      '可在此报告数据、来源、模型、分析结论、账号与个人信息问题。当前只建立共享受理入口，不宣称已有具名负责人或已承诺处理时限。',
    sections: Object.freeze([
      Object.freeze({
        title: '提交时请包含',
        paragraphs: Object.freeze([
          '问题类型（数据与来源 / 分析与模型 / 隐私与账号 / 安全 / 其他）、页面稳定链接、记录 ID 或截图、发现时间、可核验依据以及建议更正内容。',
          '涉及个人信息请求时，说明希望访问、更正、导出、删除或注销的范围。不要附上密码或 token。',
        ]),
      }),
      Object.freeze({
        title: '当前处理能力',
        paragraphs: Object.freeze([
          '共享邮箱用于受理和转交。责任人、优先级、处理状态、结论变更的下游影响追踪与公开修订记录尚未形成可验证产品能力。',
          '紧急安全问题请使用 [SECURITY] 主题。其他纠错请使用 [CORRECTION] 主题。',
        ]),
      }),
    ]),
    related: Object.freeze(['/privacy', '/terms', '/security', '/methodology', '/sources']),
  }),
})

export const GOVERNANCE_PATHS = Object.freeze(Object.keys(PUBLIC_GOVERNANCE_PAGES))

export function getGovernancePage(path) {
  return PUBLIC_GOVERNANCE_PAGES[path] || PUBLIC_GOVERNANCE_PAGES['/corrections']
}

export function buildGovernanceMailto(subject = '[CORRECTION] GlobeMind 问题报告') {
  return `mailto:${GOVERNANCE_CONTACT.email}?subject=${encodeURIComponent(subject)}`
}
