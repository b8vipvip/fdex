# FDEX Server

FastAPI 服务端基础工程。

## 启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## 测试

```bash
PYTHONPATH=. pytest -q
```
