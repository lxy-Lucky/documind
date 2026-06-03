# DocuMind

本地 Excel 知识库检索系统。支持仕様書、设计文档、数据字典等多类型 Excel，混合检索 + LLM 阅读理解，跨语言（中/日/英）问答。

## 架构概览

- **后端**：Python 3.12 + FastAPI + SQLite (sqlite-vec + FTS5)
- **LLM**：本地 Ollama，Qwen3-14B（问答）+ Qwen2.5-VL-7B（视觉）
- **Embedding**：BGE-M3（通过 FastEmbed）
- **Reranker**：bge-reranker-v2-m3
- **分词**：Lindera（日语为主，多语言兼容）
- **前端**：Vue 3 + Vite + TypeScript + TailwindCSS

## 目录结构

```
documind/
├── backend/        # FastAPI 后端
│   ├── main.py
│   ├── config.py
│   ├── db/         # 数据库 schema & 连接
│   ├── llm/        # Ollama / Embedding / Reranker 客户端
│   ├── ingestion/  # 上传流水线 & 各类 sheet 解析器
│   ├── retrieval/  # 混合检索 & rerank
│   └── api/        # HTTP 路由
├── frontend/       # Vue 3 前端（后续）
└── data/           # 上传文件 / 截图 / SQLite db（运行时生成）
```

## Linux 端快速启动

### 1. 系统依赖

```bash
# LibreOffice headless（生成 sheet 截图用）
sudo apt-get install -y libreoffice poppler-utils

# Python 3.12
sudo apt-get install -y python3.12 python3.12-venv
```

### 2. Ollama 模型准备

```bash
# 假设 Ollama 已安装并运行 (http://localhost:11434)
ollama pull qwen3:14b
ollama pull qwen2.5vl:7b
```

### 3. 后端

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 按需编辑

# 启动
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. 健康检查

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/health/deps   # 详细依赖检查
```

`/api/health/deps` 会逐项报告：Ollama 是否可达、qa/vl 模型是否已 pull、sqlite-vec 扩展是否加载成功，方便排错。

## 开发说明

- 开发机不需要可运行环境，仅写代码；测试在 Linux 机器上跑。
- 任意配置走 `.env`，代码内通过 `config.settings` 访问。
- 数据库首次启动自动建表（`db/schema.sql` + `init_db()`）。
