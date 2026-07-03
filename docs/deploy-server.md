# 云服务器部署说明

推荐软件组合：

- 云服务器系统：Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS
- 服务器运行方式：Docker Compose
- 远程连接：Windows PowerShell 自带 `ssh`
- 开放端口：`8080`
- 必备密钥：`GIPHY_API_KEY`
- 可选国内源密钥：`ALAPI_TOKEN`
- 可选视觉识别密钥：`OPENAI_API_KEY`

## 1. 购买服务器

最低配置即可：

- 1 核 CPU
- 1 GB 内存
- 20 GB 硬盘
- Ubuntu 22.04/24.04

安全组/防火墙需要放行：

- `22`：SSH 登录
- `8080`：StickerSaver 服务

## 2. 登录服务器

```powershell
ssh root@你的服务器IP
```

## 3. 安装 Docker

在服务器上执行：

```bash
apt update
apt install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 4. 拉取项目

```bash
git clone https://github.com/Mmm-ny/sticker-saver.git
cd sticker-saver
```

## 5. 配置 API Key

```bash
cp server/.env.example .env
nano .env
```

最少需要配置 GIPHY，国内表情源可以后面再加：

```text
GIPHY_API_KEY=你的真实GIPHY_API_KEY
ALAPI_TOKEN=
OPENAI_API_KEY=
OPENAI_VISION_MODEL=gpt-4.1-mini
OPENAI_VISION_FALLBACK_MODEL=gpt-4o-mini
```

如果已经在 ALAPI 申请了“斗图表情包”接口，把 `ALAPI_TOKEN` 改成你的真实 token。服务端会优先搜索 ALAPI，没配置、无结果或接口失败时自动回退到 GIPHY。

如果要启用“根据本地图片/视频画面搜索”，还需要配置 `OPENAI_API_KEY`。手机端会上传图片或视频首帧到服务端，服务端生成中文搜索词后再搜索表情包；密钥只保存在服务器 `.env`，不要提交到 GitHub。

## 6. 启动服务

```bash
docker compose up -d --build
```

检查状态：

```bash
docker compose ps
curl http://127.0.0.1:8080/health
```

如果返回 `{"ok": true}`，说明服务已启动。

## 7. 手机 App 填写地址

Android App 首页的“服务端地址”填写：

```text
http://你的服务器IP:8080
```

然后搜索“哈哈”测试。

## 常用命令

查看日志：

```bash
docker compose logs -f
```

重启：

```bash
docker compose restart
```

停止：

```bash
docker compose down
```

更新代码：

```bash
git pull
docker compose up -d --build
```
