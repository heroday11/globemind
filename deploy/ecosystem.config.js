/**
 * PM2 应用根目录：默认与 README 中模型路径一致（/root/data/globemind）。
 * 若代码部署在 /data/globemind，启动前执行: export GLOBEMIND_HOME=/data/globemind
 */
const GLOBEMIND_HOME = process.env.GLOBEMIND_HOME || "/root/data/globemind";
const LOG_DIR = `${GLOBEMIND_HOME}/logs`;

module.exports = {
  apps: [
    {
      name: "unified-models",
      cwd: GLOBEMIND_HOME,
      script: "/opt/conda/envs/Globemind_env/bin/python",
      args: "-m uvicorn api_services.unified_models_service:app --host 0.0.0.0 --port 8001",
      autorestart: true,
      watch: false,
      out_file: `${LOG_DIR}/pm2-unified-models.out.log`,
      error_file: `${LOG_DIR}/pm2-unified-models.err.log`,
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        HF_ENDPOINT: "https://hf-mirror.com",
        PYTORCH_CUDA_ALLOC_CONF: "expandable_segments:True,max_split_size_mb:256",
        HF_HUB_OFFLINE: "1",
        TRANSFORMERS_OFFLINE: "1",
        NO_PROXY: "127.0.0.1,localhost",
        no_proxy: "127.0.0.1,localhost",
      },
    },
    {
      name: "llm-vllm",
      cwd: GLOBEMIND_HOME,
      script: "bash",
      args: "./deploy/start_llm.sh",
      autorestart: false,
      watch: false,
      out_file: `${LOG_DIR}/pm2-llm-vllm.out.log`,
      error_file: `${LOG_DIR}/pm2-llm-vllm.err.log`,
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        NO_PROXY: "127.0.0.1,localhost",
        no_proxy: "127.0.0.1,localhost",
        // 默认用于 LLM domain 二判：短上下文、高吞吐、较低 KV cache 压力。
        // 可被宿主 export + pm2 restart --update-env 覆盖：
        // VLLM_PROFILE=event_extract 或 long_context
        VLLM_PROFILE: process.env.VLLM_PROFILE || "domain_judge",
        VLLM_GPU_MEMORY_UTILIZATION:
          process.env.VLLM_GPU_MEMORY_UTILIZATION || "0.62",
        VLLM_MAX_MODEL_LEN: process.env.VLLM_MAX_MODEL_LEN || "2048",
        VLLM_MAX_NUM_SEQS: process.env.VLLM_MAX_NUM_SEQS || "128",
        VLLM_MAX_NUM_BATCHED_TOKENS:
          process.env.VLLM_MAX_NUM_BATCHED_TOKENS || "16384",
      },
    },
    {
      name: "ground-news-image-backfill",
      cwd: GLOBEMIND_HOME,
      script: "bash",
      args: "./deploy/ground_news_image_backfill_loop.sh",
      autorestart: true,
      watch: false,
      out_file: `${LOG_DIR}/pm2-ground-news-image-backfill.out.log`,
      error_file: `${LOG_DIR}/pm2-ground-news-image-backfill.err.log`,
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHON_BIN: "/opt/conda/envs/Globemind_env/bin/python",
        INTERVAL_SECONDS: process.env.GROUND_NEWS_IMAGE_INTERVAL_SECONDS || "1800",
        CLUSTER_LIMIT: process.env.GROUND_NEWS_IMAGE_CLUSTER_LIMIT || "500",
        NEWS_PER_CLUSTER: process.env.GROUND_NEWS_IMAGE_NEWS_PER_CLUSTER || "6",
        WORKERS: process.env.GROUND_NEWS_IMAGE_WORKERS || "12",
        TIMEOUT: process.env.GROUND_NEWS_IMAGE_TIMEOUT || "8",
        L1_RUN_ID: process.env.GROUND_NEWS_L1_RUN_ID || "fast_l1_v2",
        L15_RUN_ID: process.env.GROUND_NEWS_L15_RUN_ID || "fast_l15_v1",
      },
    },
    {
      name: "ground-news-realtime-refresh",
      cwd: GLOBEMIND_HOME,
      script: "bash",
      args: "./deploy/ground_news_realtime_refresh_loop.sh",
      autorestart: true,
      watch: false,
      out_file: `${LOG_DIR}/pm2-ground-news-realtime-refresh.out.log`,
      error_file: `${LOG_DIR}/pm2-ground-news-realtime-refresh.err.log`,
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHON_BIN: "/opt/conda/envs/Globemind_env/bin/python",
        INTERVAL_SECONDS: process.env.GROUND_NEWS_REALTIME_INTERVAL_SECONDS || "1800",
        LOOKBACK_DAYS: process.env.GROUND_NEWS_REALTIME_LOOKBACK_DAYS || "7",
        FUTURE_DAYS: process.env.GROUND_NEWS_REALTIME_FUTURE_DAYS || "1",
        MAX_CANDIDATES: process.env.GROUND_NEWS_REALTIME_MAX_CANDIDATES || "650",
        MIN_CHAIN_SEGMENTS: process.env.GROUND_NEWS_REALTIME_MIN_CHAIN_SEGMENTS || "2",
        L1_RUN_ID: process.env.GROUND_NEWS_L1_RUN_ID || "fast_l1_v2",
        L15_RUN_ID: process.env.GROUND_NEWS_L15_RUN_ID || "fast_l15_v1",
        L2_RUN_ID: process.env.GROUND_NEWS_L2_RUN_ID || "fast_l2_v1",
      },
    },
  ],
};
