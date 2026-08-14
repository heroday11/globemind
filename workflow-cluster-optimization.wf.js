/**
 * Cluster Optimization Dynamic Workflow
 * 
 * Multi-agent pipeline for L1/L2 clustering system optimization.
 * Agents work in parallel where possible, with quality gates between steps.
 * 
 * Architecture:
 *   topic-engineer ──┐
 *                     ├──→ code-review ──→ tester ──→ integration
 *   classifier-engineer ─┘
 *                       
 *   eval-engineer ──────────────────────────────────────────→ (parallel)
 *   reporter ───────────────────────────────────────────────→ (final)
 */

const workflow = {
  name: "Cluster Optimization Pipeline",
  
  // Agent definitions
  agents: {
    "topic-engineer": {
      model: "default",
      system: `你是一个NLP算法工程师。你的任务是对全局28K地缘文章运行话题先验聚类。
        使用 /root/data/globemind/core_pipeline/topic_clustering.py 模块。
        步骤:
        1. 从 /root/data/globemind/data/checkpoint_v11_240k.jsonl 加载28K文章
        2. 从数据库加载BGE-M3 embeddings
        3. 运行 cluster_topics() 得到话题分配
        4. 对每个话题，运行 build_event_coreference_with_embeddings(use_classifier=True)
        5. 保存结果到 event_coref_clusters 表
        6. 输出指标: 总簇数、非单例数、耗时`
    },
    "classifier-engineer": {
      model: "default",
      system: `你是一个机器学习工程师。训练并验证文档对分类器。
        使用 /root/data/globemind/core_pipeline/document_classifier.py。
        步骤:
        1. 从现有 L1 聚类结果生成正例/负例对
        2. 用 BGE-M3 embeddings 训练逻辑回归分类器
        3. 评估: Accuracy, F1, AUC
        4. 保存模型到 data/models/document_classifier.joblib
        5. 必须确保 AUC >= 0.80 否则重训`
    },
    "eval-engineer": {
      model: "default",
      system: `你是一个评测工程师。运行 ECB+ 标准评测。
        使用 /root/data/globemind/scripts/eval_ecb_plus.py。
        步骤:
        1. 确保 ECB+ 数据已加载
        2. 运行 L1 聚类生成 pred_layer1.jsonl
        3. 运行 evaluate 计算 MUC/B3/CEAF/BLANC
        4. 输出与 Cattan et al. 2021 的对比表
        5. 保存结果`
    },
    "code-reviewer": {
      model: "default",
      system: `你是一个严格的代码审查者。审查所有新建和修改的代码。
        检查: Python最佳实践、类型安全、错误处理、性能、SQL注入防护。
        必须找出至少3个改进点，否则标记为FAIL需要重做。`
    },
    "tester": {
      model: "default",
      system: `你是一个测试工程师。验证管线完整性。
        测试:
        1. 话题聚类模块能否在5K样本上正确运行
        2. 分类器推理延迟是否 < 1ms/对
        3. L1聚类能否在5K样本上完成不报错
        所有测试通过才能标记PASS。`
    },
    "reporter": {
      model: "default",
      system: `你是一个技术报告写手。汇总所有agent的结果。
        输出:
        1. L1聚类效果对比表 (优化前vs优化后)
        2. ECB+评测对比表
        3. 各模块耗时
        4. 剩余待优化项
        保存到 docs/workflow_final_report.md`
    }
  },

  // Task pipeline with quality gates
  steps: [
    {
      name: "topic-clustering",
      agent: "topic-engineer",
      input: `运行全量28K话题先验聚类（含分类器）。
        输出结果到数据库并记录指标。
        如果失败，重试最多3次，每次间隔30秒。`,
      retry: 3,
      retryDelay: 30,
      qualityGate: "non_singleton_rate > 8%" // 必须高于优化前的8%
    },
    {
      name: "classifier-training",
      agent: "classifier-engineer", 
      input: `从最新的L1聚类结果训练文档对分类器。
        AUC必须 >= 0.80。`,
      retry: 2,
      qualityGate: "auc >= 0.80"
    },
    {
      name: "ecb-evaluation",
      agent: "eval-engineer",
      input: `运行ECB+评测并输出对比表。`,
      retry: 2
    },
    {
      name: "code-review",
      agent: "code-reviewer",
      dependsOn: ["topic-clustering", "classifier-training"],
      input: `审查所有新增和修改的代码。
        必须找到至少3个改进点。`,
      qualityGate: "issues >= 3"
    },
    {
      name: "testing",
      agent: "tester",
      dependsOn: ["code-review"],
      input: `在5K样本上验证全管线。
        所有测试必须通过。`
    },
    {
      name: "final-report",
      agent: "reporter",
      dependsOn: ["testing", "ecb-evaluation"],
      input: `汇总所有结果到 docs/workflow_final_report.md`
    }
  ]
};

export default workflow;
