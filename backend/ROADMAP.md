# 涉华舆情系统质量优化路线图

## 现状速览

| 维度 | 当前状态 | 目标 |
|------|---------|------|
| 涉华分类精度 | XLM-R **97% F1**（已验证）但 API 读的是 BGE+LR（AUC 0.88） | XLM-R 97% 贯穿全链路 |
| 情感方向 | FinBERT，**从未验证** | 准确率 ≥ 85% |
| 框架分类 | LLM 8类，**无混淆矩阵** | 每类 F1 ≥ 80% |
| 事件回测 | 脚本写了但**从未跑出结果** | 加权命中率 ≥ 90% |
| 数据延迟 | 管线写 news_analysis，回填脚本写 news_ai_analysis，**间隔未知** | 实时写入 < 1min |
| API 缓存 | 3 个端点加了内存缓存 | 全部高频端点覆盖 |

---

## Phase 0 — 统一数据流（P0，2-3 天）

**目标：XLM-R 97% F1 的模型贯穿全链路，消除管线/API 的分数断裂。**

### 0.1 XLM-R 分数写入 china_relevance_score
**文件：** `analysis_service.py` — `_compute_china_index_only()` + `_write_back_batch()`
**改动：** 当前 XLM-R 分只写 `news_analysis.china_related_index`（管线表），改为同时写入 `news_ai_analysis.china_relevance_score`（评分表，SMALLINT 0-8）
```python
# 在 _write_back_batch() 末尾追加
INSERT INTO news_ai_analysis (news_id, china_relevance_score, ...)
VALUES (%s, round(xlm_score * 8), ...)
ON CONFLICT (news_id) DO UPDATE
```
**验证：** `SELECT china_relevance_score FROM news_ai_analysis LIMIT 5` 非空

### 0.2 API 端点统一读取 china_relevance_score
**文件：** `opinion.py` — 全部 13 个涉华端点
**改动：** `COALESCE(na.prototype_weighted, 0)` → `COALESCE(na.china_relevance_score::double precision / 8.0, 0)`
**注意：** `prototype_weighted` 仅 `globemind_news` 有，`china_relevance_score` 两库都有
**验证：** `python scripts/backtest_events.py --days 730` 结果不变差

### 0.3 管线增量直写 news_ai_analysis（消除双表）
**改动：** 管线写完 `news_analysis` 后立即写 `news_ai_analysis`，同一事务
**效果：** 新入库新闻即时可在 API 查询，无需等离线回填
**验证：** 新插入一条新闻，API 秒级可查

### 0.4 backfill_prototypes 降级
**改动：** `prototype_weighted` 不再存 BGE+LR 评分，改存 6 维余弦加权和（仅可解释性参考）
`backfill_llm_china_relevance.py` 的 LLM 评分改写到 LLM 专用字段

### 0.5 移除硬编码密码
**文件：** `analysis_service.py:221-240`
**改动：** `_pg_read()/_pg_write()` 去掉默认密码，全部走环境变量

### 验收标准
- `china_relevance_score` 缺失率 < 1%
- `china-trend` 等端点的 SQL 零引用 `prototype_weighted`
- `data_quality_check --all-dbs` 全部正常

---

## Phase 1 — 精度验证与修复（P0，3-5 天）

**目标：每个维度的精度可测量、可达标。**

### 1.1 回测运行 + 修复（第 1 天）
**文件：** `scripts/backtest_events.py`
**操作：** 先跑 baseline → 修到加权命中率 ≥ 90%
- 当前 13 个事件的方向 + 冲击检测
- 记录每个事件的 `event_impact`、`baseline`、`percentile`
- 对 FAIL 事件分析根因（情感方向反了？涉华权重太低？日期不对？）
- 修复后固定回测结果为基线

### 1.2 情感方向抽样验证（第 2 天）
**操作：** 从 DB 抽 200 条涉华新闻（中英各 100），人工标正/负/中立
- 对比 FinBERT 输出的 `china_impact_sentiment`
- 计算准确率、正类召回率、负类召回率
- `expected`: FinBERT 把"Trump 加征关税"标 POSITIVE 的比例
- `fix`: 若 < 80%，考虑加一个中国视角 polarity 映射层，或改用 LLM 做三元情感

### 1.3 框架分类混淆矩阵（第 2-3 天）
**操作：** 抽 200 条/框架（共 1600 条），对比 LLM 输出 vs 人工标
- 输出 8×8 混淆矩阵
- 识别最容易混淆的框架对（如"科技竞争"↔"经济合作"、"军事冲突"↔"中国威胁论"）
- `fix`: 对高频混淆对优化 LLM prompt examples

### 1.4 词典权重校准（第 3 天）
**操作：** 在 XLM-R 验证集上对比 lexicon 各层权重的贡献
- 检查 0.40/0.25/0.20/0.08 是否合理
- 如果 lexicon 与 XLM-R 在某个子集上严重分歧，调整权重或降低 lexicon 系数

### 验收标准
- `backtest_events.py` 加权命中率 ≥ 90%（记录到文件）
- 情感方向准确率 ≥ 85%（抽样 200 条）
- 框架分类 Macro F1 ≥ 80%（1600 条抽样）
- 上述指标有文档记录，可复现

---

## Phase 2 — 质量门禁（P1，3-4 天）

**目标：精度不退化，故障可感知。**

### 2.1 API 集成测试
**文件：** `tests/test_opinion_api.py`
- 用 FastAPI TestClient 测试 13 个涉华端点的响应格式和状态码
- mock DB 返回已知数据，验证指数计算正确性

### 2.2 回测自动化
- CI/CD 中每次数据更新后自动运行 `backtest_events.py`
- 加权命中率 < 85% 告警
- 结果保存到文件，可追溯历史趋势

### 2.3 数据质量告警
- `data_quality_check.py` 接入 cron（每 30 分钟）
- 缺失率超阈值 → webhook 通知
- 数据延迟 > 24h → 告警

### 2.4 结构化日志
- `analysis_service.py` 中 `print()` → `logging.getLogger(__name__)`
- 关键节点记录耗时、行数、模型分数分布

### 验收标准
- `pytest tests/ -v` 全部通过
- `data_quality_check` 接入 cron
- `backtest_events.py` 有历史运行记录

---

## Phase 3 — 高级增强（P2，按需）

### 3.1 LLM 评分接入实时管线
- 当前 Phase 2.3 的 LLM china relevance 只是离线回填脚本
- 改为在 `_run_llm_pipeline()` 中一并调用（复用已有 vLLM 请求）
- 0.35 权重与 XLM-R 97% F1 做加权

### 3.2 情感校准层
- 如果在 Phase 1.2 中发现 FinBERT 偏差严重
- 在 FinBERT 输出上加一个 lightweight 映射（50 条标注即可训练）
- 或用 LLM（Qwen2.5）做三元情感替代

### 3.3 模型持续训练
- 定期用新标注数据微调 XLM-R
- 回测结果作为回归测试

### 3.4 A/B 实验框架
- 同时运行两版评分，对比 API 输出
- 评估新版是否更优再做全量切换

---

## 优先级建议

```
现在开始 → Phase 0.1 + 0.2（统一分数）→ 1天
          Phase 1.1（跑回测看实际水平）→ 半天
          根据回测结果决定：如果命中率已经 > 85%
            → Phase 0.3 + 0.4（消除双表）
            → Phase 1.2 + 1.3（精修各维度）
          如果命中率 < 70%
            → 先修情感方向（Phase 1.2）再做其他
```

要我先从 Phase 0.1 开始改？
