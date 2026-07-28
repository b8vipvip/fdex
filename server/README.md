# FDEX Server

FastAPI 服务端基础工程。

## 配置

```bash
cp .env.example .env
```

默认仅监听本机：

```dotenv
FDEX_HOST=127.0.0.1
FDEX_PORT=18080
FDEX_WORKERS=2
```

端口被占用时，修改 `FDEX_PORT`，不要结束不属于 FDEX 的进程。

## 启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.run
```

健康检查：

```bash
curl http://127.0.0.1:18080/api/health
```

## 测试

```bash
PYTHONPATH=. pytest -q
```
