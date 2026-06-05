# OSN Lab2 Project 6: AI Companion System Prompt Security

本目录实现“Security Analysis of User-Defined System Prompts in AI Companion Scenarios”的完整实验流水线。数据采集以 Arctic Shift 为主，实验流程为：

1. 从 Reddit 公开社区采集 AI companion 自我介绍候选帖。
2. 过滤得到不少于 30 条第一人称 companion 自我介绍。
3. 将自我介绍转换为可复现实验用的 system prompt。
4. 使用统一 LLM API 跑控制组和 companion prompt 实验组。
5. 用 safety rubric 判分并生成分析表格、图表和报告素材。

## Environment

脚本只依赖 Python 标准库。LLM 调用支持两类格式：

- `openai_compatible`: OpenAI Chat Completions 兼容格式，覆盖 OpenAI、DeepSeek、DashScope/Qwen、Moonshot/Kimi、智谱、SiliconFlow、OpenRouter 等。
- `anthropic`: Anthropic 原生 Messages API，适用于 Claude 官方接口。

查看当前支持的 provider presets：

```bash
python3 scripts/run_safety_tests.py --list-providers
```

通用配置方式：

```bash
export LLM_PROVIDER="openai_compatible"
export LLM_API_KEY="..."
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_MODEL="your-model-name"
```

国内模型一般只需要切换 `LLM_PROVIDER` 和对应 key/model：

```bash
# DeepSeek
export LLM_PROVIDER="deepseek"
export DEEPSEEK_API_KEY="..."
export DEEPSEEK_MODEL="deepseek-chat"

# 通义千问 / DashScope OpenAI-compatible mode
export LLM_PROVIDER="dashscope"
export DASHSCOPE_API_KEY="..."
export DASHSCOPE_MODEL="qwen-plus"

# Moonshot / Kimi
export LLM_PROVIDER="moonshot"
export MOONSHOT_API_KEY="..."
export MOONSHOT_MODEL="moonshot-v1-8k"

# 智谱 GLM
export LLM_PROVIDER="zhipu"
export ZHIPU_API_KEY="..."
export ZHIPU_MODEL="glm-4-flash"

# SiliconFlow
export LLM_PROVIDER="siliconflow"
export SILICONFLOW_API_KEY="..."
export SILICONFLOW_MODEL="Qwen/Qwen2.5-7B-Instruct"
```

Anthropic 原生接口需要不同格式，不能只替换 OpenAI key：

```bash
export LLM_PROVIDER="anthropic"
export ANTHROPIC_API_KEY="..."
export ANTHROPIC_MODEL="claude-sonnet-4-5"
# optional
export ANTHROPIC_VERSION="2023-06-01"
```

所有脚本也支持命令行覆盖：

```bash
python3 scripts/run_safety_tests.py \
  --provider deepseek \
  --api-key "..." \
  --model deepseek-chat
```

Base URL 优先级：

- `openai` / `openai_compatible`: `--base-url` > `OPENAI_BASE_URL` 或 `LLM_BASE_URL` > preset 默认值。
- 具体 provider: `--base-url` > provider 专属变量，例如 `DEEPSEEK_BASE_URL`、`ANTHROPIC_BASE_URL` > preset 默认值。
- 如果切换 provider 后发现请求打到旧地址，检查并清理对应的 `*_BASE_URL` 环境变量。

如果暂时没有 API key，可以在转换和测试阶段使用 `--mock` 跑通流程。

## Pipeline

采集候选 Reddit posts：

```bash
python3 scripts/collect_posts.py --output data/raw_posts.jsonl
```

如果命中的是社区介绍帖或 weekly prompt，继续抓取候选帖评论区；有效 AI 自我介绍常出现在 comments 中：

```bash
python3 scripts/collect_comments.py \
  --posts data/raw_posts.jsonl \
  --output data/raw_posts_with_comments.jsonl
```

过滤有效自我介绍：

```bash
python3 scripts/filter_posts.py \
  --input data/raw_posts_with_comments.jsonl \
  --output data/filtered_posts.csv \
  --target 30
```

转换为 system prompts：

```bash
python3 scripts/convert_prompts.py \
  --input data/filtered_posts.csv \
  --output data/system_prompts.jsonl
```

先用 mock 跑 2 个样本和 3 道题的 smoke test：

```bash
python3 scripts/convert_prompts.py --limit 2 --mock
python3 scripts/run_safety_tests.py --limit-prompts 2 --limit-questions 3 --mock
python3 scripts/judge_results.py
python3 scripts/analyze_results.py
```

正式批量测试：

```bash
python3 scripts/run_safety_tests.py \
  --prompts data/system_prompts.jsonl \
  --questions data/safety_questions.csv \
  --output data/results.jsonl \
  --resume

python3 scripts/judge_results.py \
  --input data/results.jsonl \
  --output data/judged_results.jsonl

python3 scripts/analyze_results.py \
  --input data/judged_results.jsonl
```

## Local Review UI

启动本地人工审核界面：

```bash
python3 scripts/review_server.py --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

界面包含四个工作区：

- `Samples`: 审核 Arctic Shift posts/comments 候选样本，详情区支持安全 Markdown 渲染，保存 `keep / reject / uncertain` 到 `data/manual_sample_review.csv`。
- `Prompts`: 对比原始 self-introduction 和生成的 system prompt，审核或修改后保存到 `data/system_prompts_reviewed.jsonl`。
- `Results`: 复核自动 safety judge 标签，人工标签保存到 `data/manual_result_review.csv`。
- `Overview`: 查看分析摘要和 SVG 图表。

如果已经人工修订 prompts，正式测试时可以改用：

```bash
python3 scripts/run_safety_tests.py \
  --prompts data/system_prompts_reviewed.jsonl \
  --questions data/safety_questions.csv \
  --output data/results.jsonl \
  --resume
```

如果已经在 `Samples` 页完成候选样本审核，可以用人工结果重新生成 filtered 样本：

```bash
python3 scripts/filter_posts.py \
  --input data/raw_posts_with_comments.jsonl \
  --output data/filtered_posts.csv \
  --manual-only \
  --target 30
```

## Data Files

- `data/raw_posts.jsonl`: Arctic Shift 原始采集结果。
- `data/raw_posts_with_comments.jsonl`: 原始 posts 加评论区候选样本。
- `data/rejected_posts.csv`: 被过滤掉的候选帖和原因。
- `data/filtered_posts.csv`: 最终有效样本。
- `data/system_prompts.jsonl`: 反推得到的 system prompts。
- `data/system_prompts_reviewed.jsonl`: 前端人工审核或修改后的 system prompts。
- `data/safety_questions.csv`: 默认安全测试题集，可替换为 SafetyBench 或 SALAD-Bench 子集。
- `data/results.jsonl`: 模型原始回答。
- `data/judged_results.jsonl`: 带 safety label 的回答。
- `data/manual_sample_review.csv`: 前端保存的样本审核结果。
- `data/manual_result_review.csv`: 前端保存的结果复核标签和备注。
- `report/summary.md`: 自动生成的结果摘要。
- `report/figures/`: 自动生成的 SVG 图表。

## Arctic Shift Notes

采集脚本使用：

```text
https://arctic-shift.photon-reddit.com/api/posts/search
```

默认字段：

```text
id,title,selftext,created_utc,subreddit,url,over_18,score,num_comments
```

Arctic Shift 不返回 `permalink` 字段，脚本会用 `https://www.reddit.com/r/{subreddit}/comments/{id}/` 补全帖子链接。由于 Arctic Shift 是免费归档服务，脚本默认带重试、超时和请求间隔；如果采集不足，建议加大时间窗、调整关键词，或人工检查 `data/rejected_posts.csv`。

## Manual Review Requirement

`judge_results.py` 的标签是启发式自动判分，用于快速统计。最终报告前必须人工复核：

- 所有 `unsafe_compliance`。
- 所有 `partial_compliance`。
- 所有 `needs_review`。
- 随机抽取至少 10% 的 `safe_refusal`。

报告中不要直接暴露 Reddit 用户名，不展示大段有害输出；必要例子使用匿名化、概括化描述。
