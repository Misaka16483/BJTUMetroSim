# 启动说明

## 后端 API 服务器

```bash
# Windows CMD
start_backend.bat

# Git Bash / WSL
bash start_backend.sh
```

**环境准备**（首次运行前执行一次）：
```bash
uv venv .venv --python 3.11
uv pip install numpy flask
```

后端启动后默认监听 `http://127.0.0.1:8000`。

## 前端开发服务器

```bash
cd bj-metro-sim
npm run dev
```

前端启动后默认监听 `http://localhost:5173`。

## 操作顺序

1. 启动后端（`start_backend.bat`）
2. 启动前端（`npm run dev`）
3. 浏览器打开前端 → 点击 **"▶ 启动"** → 再添加列车
