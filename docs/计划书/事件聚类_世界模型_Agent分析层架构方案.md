# 事件聚类层 -> 世界模型层 -> Agent 分析层架构方案

## 1. 目标定义

当前 GlobeMind 已经具备以下能力：

- 新闻级事件提取
- L1 事件共指聚类
- L2 故事线聚合
- 涉华判别、情感、主题、框架分类
- 指数聚合与前端展示

下一阶段目标不是继续堆叠文章级分类器，而是把系统升级为一个可做研判和预测的结构化分析平台。

核心目标：

1. 将新闻流压缩为可计算的事件状态变化
2. 将事件状态进一步映射为国际关系中的动态世界状态
3. 基于世界状态，用 Agent 进行假设生成、证据拼接、情景模拟和预测输出
4. 建立预测评估与复盘闭环，避免系统退化为“会讲故事的报告生成器”


## 2. 总体分层

建议采用四层结构：

### 2.1 观测层

输入：

- 新闻文本
- 媒体元数据
- 已有情感 / topic / frame / china index
- 外部补充信号（可选）
  - 市场数据
  - 制裁名单
  - 军事活动
  - 官方声明

职责：

- 把原始噪声输入转成可归一化的事件观测

### 2.2 事件层

输入：

- v11 事件提取结果
- L1 事件簇
- L2 story / micro-story

职责：

- 构建“发生了什么”的结构化事件图
- 输出标准事件对象和事件链

输出对象建议：

```json
{
  "event_id": "story_123_node_7",
  "event_type": "military",
  "actors": ["China", "Philippines"],
  "targets": ["South China Sea"],
  "time_range": ["2026-06-10", "2026-06-14"],
  "location": "South China Sea",
  "intensity": 0.72,
  "sentiment_toward_china": -0.64,
  "topic": "南海摩擦",
  "frame": "军事冲突",
  "evidence_count": 18
}
```

### 2.3 世界模型层

职责：

- 把事件流映射成“国际系统状态”的更新
- 维护可演化的实体状态、关系状态、风险状态

这是分析层的核心，不应交给 LLM 隐式完成。

### 2.4 Agent 分析层

职责：

- 在世界模型之上执行分析任务
- 输出结构化预测、情景路径、关键触发条件和反证


## 3. 事件聚类层改造建议

现有 L1/L2 已能形成事件簇和故事线，但若要支撑预测，还需要把事件层变成“状态更新输入”，而不是仅供展示。

建议补三类字段：

### 3.1 事件强度

定义事件的影响幅度，建议综合：

- 媒体加权声量
- 来源可信度
- 涉华指数
- 边类型
- 同类事件历史稀有度

可形成统一 `event_intensity_score ∈ [0,1]`

### 3.2 事件方向

不仅要知道“负面/正面”，还要知道方向作用在谁身上。

至少拆分：

- toward_china
- toward_counterparty
- toward_system_stability

示例：

- “美对华芯片禁令升级”
  - toward_china = negative
  - toward_US = neutral / mixed
  - toward_system_stability = negative

### 3.3 事件语义标签

除了 `event_type`，建议加上更适合状态转移的标签：

- coercion
- signaling
- alliance
- sanctions
- tech_restriction
- trade_retaliation
- military_probe
- diplomatic_deescalation

这些标签将直接决定世界模型中的状态更新规则。


## 4. 世界模型设计

## 4.1 设计原则

世界模型不应是纯文本记忆，也不应只是知识图谱。

建议定义为：

“一组实体、关系和风险变量组成的动态状态空间，新闻事件只是对这些隐藏状态的观测与更新。”

## 4.2 状态对象

### 4.2.1 实体状态

针对国家 / 地区 / 组织：

- political_stability
- military_posture
- economic_pressure
- diplomatic_activity
- domestic_attention_to_china
- external_attention_to_china

### 4.2.2 双边关系状态

针对 actor pair：

- tension_level
- cooperation_level
- trade_conflict_level
- military_risk
- trust_level
- sanction_pressure

例如：

```json
{
  "pair": ["China", "US"],
  "tension_level": 0.82,
  "cooperation_level": 0.21,
  "trade_conflict_level": 0.74,
  "military_risk": 0.41,
  "last_updated": "2026-06-17T01:00:00Z"
}
```

### 4.2.3 区域风险状态

按热点区域维护：

- Taiwan Strait
- South China Sea
- Korean Peninsula
- Middle East energy corridor
- Europe-China trade corridor

字段可包括：

- escalation_probability_7d
- escalation_probability_30d
- narrative_heat
- military_signal_density
- policy_change_pressure

## 4.3 状态转移机制

推荐采用“规则 + 统计校准”的混合式方案。

### 规则层

为每种事件类型设定对状态的影响方向：

- `military_probe` 提升 `military_risk` 和 `tension_level`
- `sanctions` 提升 `economic_pressure` 和 `trade_conflict_level`
- `diplomatic_meeting` 提升 `cooperation_level`，降低部分 `tension_level`

### 统计校准层

后续用历史数据学习每类事件的真实影响幅度：

- 强度系数
- 时间衰减
- 条件依赖

示例：

```text
Delta(tension_level)
= base_weight(event_type)
* event_intensity
* actor_sensitivity
* region_sensitivity
* decay(time)
```

## 4.4 时间衰减

世界模型必须有状态衰减，否则风险只会上升不会回落。

建议：

- 高频事件：短半衰期
- 制裁/政策：长半衰期
- 军事冲突：中长半衰期

这样系统才能区分：

- 短期媒体热度
- 中期政策影响
- 长期结构性张力


## 5. Agent 分析层设计

Agent 不负责直接读原始新闻做结论，而是围绕“世界模型快照 + 事件链 + 历史案例”工作。

建议采用多 Agent 弱自治结构。

## 5.1 Agent 角色

### 5.1.1 Forecaster

职责：

- 生成未来 7 / 30 / 90 天情景
- 给出每条情景的概率和触发条件

输出示例：

- 情景 A：南海对峙继续升级，概率 0.46
- 情景 B：进入口头降温但维持高压部署，概率 0.38

### 5.1.2 Skeptic

职责：

- 专门寻找反证
- 识别“媒体噪声误导”“重复报道放大”“情绪替代现实”等问题

### 5.1.3 Historian

职责：

- 检索历史相似事件链
- 对比当前状态与历史前例的差异

### 5.1.4 RiskSynthesizer

职责：

- 汇总前述 Agent 的结果
- 生成最终结构化报告

## 5.2 Agent 输入

每个 Agent 应读同一套结构化输入：

- 当前世界状态快照
- 最近 N 天关键事件链
- 相关区域风险序列
- 关键主体关系变化
- 历史相似案例

不建议直接把 50 篇新闻全文扔给 Agent。

## 5.3 Agent 输出格式

必须强制结构化，避免空泛报告。

建议输出模板：

```json
{
  "question": "未来30天台海风险是否上升？",
  "baseline_state": {
    "region": "Taiwan Strait",
    "military_risk": 0.63,
    "tension_level": 0.77
  },
  "scenarios": [
    {
      "name": "持续升温",
      "probability": 0.44,
      "time_window": "30d",
      "triggers": [
        "高频军演延续",
        "美台高层互动升级"
      ],
      "evidence": [
        "最近14天军事事件密度上升",
        "涉华负面叙事热度抬升"
      ],
      "counter_evidence": [
        "未见正式政策升级",
        "外交对话渠道未关闭"
      ]
    }
  ],
  "confidence": 0.62
}
```


## 6. 预测任务类型

建议先做三类可落地任务。

### 6.1 升级风险预测

问题：

- 某区域是否将在 7/30 天内升级？

适用于：

- 台海
- 南海
- 中美科技制裁
- 中东涉华安全

### 6.2 二阶影响预测

问题：

- 当前事件是否会外溢到贸易、外交、军事或舆情层？

例如：

- 制裁是否会转化为供应链风险？
- 军事摩擦是否会转化为叙事升级？

### 6.3 舆情叙事迁移预测

问题：

- 当前 frame 是否会从“经济合作”转为“科技竞争”或“安全威胁”？

这类预测很适合你们现有 topic/frame 数据。


## 7. 实施路线

建议按三阶段推进。

## Phase A：结构化底座

目标：

- 让事件层能稳定输出世界模型可消费的数据

任务：

1. 为 L1/L2 事件簇补充强度、方向、语义标签
2. 定义世界模型状态 schema
3. 编写第一版状态转移规则
4. 将状态快照写入数据库

交付物：

- `world_state_snapshot`
- `world_state_transition_log`
- `event_state_update`

## Phase B：分析层原型

目标：

- 在世界模型上跑第一版 Agent 分析链

任务：

1. 历史案例检索器
2. Forecaster / Skeptic / Historian / Synthesizer 原型
3. 结构化报告生成
4. 前端展示情景预测和触发条件

交付物：

- `scenario_forecast`
- `forecast_evidence`
- `forecast_counter_evidence`

## Phase C：评估闭环

目标：

- 证明预测比基线强

任务：

1. 构建预测评测集
2. 做 Brier score / calibration / hit rate
3. 建立事后复盘与参数回调机制


## 8. 评估指标

如果没有评估闭环，世界模型 + Agent 很容易退化成“高级写作系统”。

建议至少跟踪：

### 8.1 分类与状态层

- 事件方向识别准确率
- 语义标签一致率
- 状态更新稳定性

### 8.2 预测层

- Brier score
- Top-1 场景命中率
- Top-2 场景覆盖率
- 概率校准误差

### 8.3 解释层

- 证据引用准确率
- 反证覆盖率
- 复盘一致性


## 9. 关键风险

### 风险 1：事件层噪声直接污染世界模型

对策：

- 只用高置信事件更新状态
- 引入来源可信度和多源确认

### 风险 2：Agent 过度发挥

对策：

- Agent 只能读取结构化状态
- 强制输出证据、反证和置信度

### 风险 3：世界模型规则过死

对策：

- 先规则化起步
- 后续用历史数据做校准

### 风险 4：预测无法评估

对策：

- 从一开始就定义明确预测问题和时间窗口


## 10. 建议结论

总体建议：

1. 支持引入世界模型和 Agent，但必须放在分析层，而不是替代底层提取与判别层
2. 先做“世界状态更新系统”，再做“多 Agent 研判”
3. 预测输出必须结构化，不能只生成自然语言报告
4. 必须同步建设预测评估体系，否则系统只会提升表述能力，不会提升真实判断能力

一句话总结：

> 事件聚类层负责压缩现实，世界模型层负责表示现实，Agent 分析层负责推演现实。

