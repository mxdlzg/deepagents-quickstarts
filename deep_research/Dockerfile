FROM python:3.12-slim

# 1. 获取 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 2. 安装 git
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 3. 复制依赖定义文件
COPY pyproject.toml uv.lock ./

# 4. 仅安装依赖 (关键修正)
# 使用 --no-install-project 告诉 uv 只下载安装依赖包(如 langchain, tavily 等)
# 此时不尝试构建项目本身，避免因缺少 research_agent 目录而报错
RUN uv sync --frozen --no-install-project

# 5. 复制其余所有源代码 (此时 research_agent 目录进来了)
COPY . .

# 6. 安装当前项目
# 这一步将项目本身安装到虚拟环境中
RUN uv sync --frozen

# 7. 配置启动
EXPOSE 2024
ENV PYTHONUNBUFFERED=1

# 启动
CMD ["uv", "run", "langgraph", "dev", "--host", "0.0.0.0", "--port", "2024"]
