# 宝塔面板部署 FDEX 服务端

目标结构：

```text
/opt/fdex/                  # Git 仓库
/opt/fdex/server/.env       # 服务端私密配置，不提交 Git
/etc/systemd/system/fdex.service
宝塔网站：fdex.k2n.cn -> 127.0.0.1:18080
```

FDEX 默认使用独立端口 `18080`，而不是常见的 `8000`。端口只监听本机，不需要开放服务器安全组。

## 一、首次部署

以 root 登录服务器：

```bash
cd /opt
git clone https://github.com/b8vipvip/fdex.git
cd /opt/fdex
cp server/.env.example server/.env
nano server/.env
```

至少确认：

```dotenv
APP_NAME=FDEX Server
APP_VERSION=1.0.0
ENVIRONMENT=production
PUBLIC_BASE_URL=https://fdex.k2n.cn
API_PREFIX=/api
CORS_ORIGINS=https://fdex.k2n.cn

FDEX_HOST=127.0.0.1
FDEX_PORT=18080
FDEX_WORKERS=2

AI_PROVIDER=openai_compatible
AI_BASE_URL=https://你的接口地址/v1
AI_API_KEY=你的密钥
AI_MODEL=你的模型名称
AI_TIMEOUT_SECONDS=60
```

随后执行：

```bash
cd /opt/fdex
sudo bash scripts/update_server.sh
```

更新脚本在启动前会检查 `FDEX_PORT`。如果该端口已经被其他服务使用，脚本只显示占用进程并退出，不会结束其他服务。

验证本机服务：

```bash
curl http://127.0.0.1:18080/api/health
```

## 二、端口冲突处理

检查默认端口：

```bash
ss -lntp 'sport = :18080'
```

如果有输出，在 `/opt/fdex/server/.env` 中改成其他空闲端口，例如：

```dotenv
FDEX_PORT=18081
```

然后宝塔反向代理也必须同步改为：

```text
http://127.0.0.1:18081
```

重新执行：

```bash
cd /opt/fdex
sudo bash scripts/update_server.sh
```

不要使用 `kill`、`pkill` 或结束占用进程，除非你确认那个进程本来就应该停止。

## 三、宝塔站点配置

在宝塔面板打开：

```text
网站 -> fdex.k2n.cn -> 设置 -> 反向代理
```

默认目标 URL：

```text
http://127.0.0.1:18080
```

也可以把 `deploy/baota/nginx-location.conf` 中的配置放入该站点的 `server {}` 内。

完成后申请并开启 SSL，强制 HTTPS。

外网验证：

```bash
curl https://fdex.k2n.cn/api/health
curl https://fdex.k2n.cn/api/public-config
```

浏览器可访问：

```text
https://fdex.k2n.cn/
https://fdex.k2n.cn/docs
```

## 四、以后更新服务端

每次 GitHub `main` 更新后，在服务器执行：

```bash
cd /opt/fdex
sudo bash scripts/update_server.sh
```

脚本会：

1. 备份 `server/.env`
2. 拉取 GitHub `main`
3. 恢复 `.env`
4. 读取并验证 `FDEX_PORT`
5. 停止旧的 FDEX systemd 服务
6. 检查端口是否被其他服务占用
7. 更新 Python 依赖和 systemd 服务
8. 重启 FDEX 并检查 `/api/health`

## 五、常用维护命令

```bash
systemctl status fdex
systemctl restart fdex
journalctl -u fdex -n 100 --no-pager
journalctl -u fdex -f
```

检查默认监听端口：

```bash
ss -lntp 'sport = :18080'
```

服务默认只监听 `127.0.0.1:18080`，不需要在服务器安全组中开放 `18080`。

## 六、Android 服务地址

Android 默认编译地址：

```text
https://fdex.k2n.cn
```

如需为测试环境构建不同地址：

```bash
gradle :app:assembleDebug -PSERVER_BASE_URL=https://test.example.com
```

第三方 API Key、AI 模型地址等只配置在服务器 `.env`，不要放进 Android 工程或 GitHub 仓库。
