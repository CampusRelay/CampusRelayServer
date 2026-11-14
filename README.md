# CampusRelay Server

CampusRelay 后端服务，基于 **FastAPI + SQLAlchemy + Alembic + PostgreSQL**。

## 当前功能

- FastAPI 应用入口：`app.main:app`
- 健康检查：`GET /health`
- 数据库健康检查：`GET /health/database`
- 学校管理 API：`/schools`（具体请求/响应以 OpenAPI 文档为准）
- 数据库结构由 Alembic 迁移管理
- GitHub Actions 使用 PostgreSQL 16 执行自动化测试

## 技术要求

- Python 3.12
- PostgreSQL 16（推荐；测试 CI 使用 PostgreSQL 16）
- Linux 部署环境可运行 systemd
- 生产服务监听 `127.0.0.1:8101`，由 Nginx 反向代理

## 本地开发（Windows PowerShell）

在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

设置数据库连接环境变量。数据库必须使用 PostgreSQL + psycopg：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://用户名:密码@127.0.0.1:5432/campusrelay"
```

启动开发服务器：

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问：

- API 根路径：<http://127.0.0.1:8000/>
- 健康检查：<http://127.0.0.1:8000/health>
- Swagger UI：<http://127.0.0.1:8000/docs>

## 数据库迁移

配置好 `DATABASE_URL` 后：

```powershell
alembic upgrade head
```

查看当前迁移版本：

```powershell
alembic current
```

创建新迁移（模型变更后）：

```powershell
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

不要在生产环境使用 `Base.metadata.create_all()` 代替 Alembic 迁移。

## 测试

测试会使用名称固定为 `campusrelay_test` 的专用 PostgreSQL 数据库，并在测试结束时回退迁移、清理测试结构。**不要把生产数据库连接字符串用于测试**。

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:密码@127.0.0.1:5432/campusrelay"
pytest -q
```

GitHub Actions 的 CI 使用临时 PostgreSQL 16 服务，不依赖开发者本机数据库。

## 生产部署架构

生产环境与现有网站/监控服务隔离：

| 项目 | 配置 |
|---|---|
| 应用目录 | `/opt/campus-relay` |
| 上传暂存目录 | `/opt/campus-relay/incoming` |
| 版本目录 | `/opt/campus-relay/releases/<commit-sha>` |
| 当前版本软链接 | `/opt/campus-relay/current` |
| 环境变量文件 | `/etc/campus-relay/server.env` |
| systemd 服务 | `campusrelay-api.service` |
| Uvicorn 监听 | `127.0.0.1:8101` |
| 健康检查 | `http://127.0.0.1:8101/health` |

部署通过 GitHub Actions、`cloudflared access ssh` 和 `rsync` 传输文件。SSH 主机沿用 `ssh.orlyn.top`，不会要求 Cloudflare Service Token。

### 首次服务器准备

先确认服务器已经安装 Python 3、venv、rsync、curl、PostgreSQL，并且 `user` 账户能够通过原有 Cloudflare SSH 方式登录。不要在未确认 PostgreSQL 状态前重复安装或覆盖配置。

把仓库文件传到服务器后，在仓库根目录执行一次：

```bash
sudo bash deploy/server-setup.sh
```

该脚本只创建 CampusRelay 独立目录、安装独立 systemd unit，并为 `user` 配置仅限 CampusRelay 服务 restart/stop/is-active 的免密码 sudo 权限；不会修改现有 Nginx 站点、Cloudflare Tunnel、8000 端口服务或 `/var/www/filehub`。

创建生产环境文件：

```bash
sudo install -d -m 750 -o root -g user /etc/campus-relay
sudo install -m 640 -o root -g user /dev/null /etc/campus-relay/server.env
sudo nano /etc/campus-relay/server.env
```

文件内容示例（请替换为真实数据库账号和密码，不要提交到 Git）：

```dotenv
DATABASE_URL=postgresql+psycopg://campusrelay_app:替换密码@127.0.0.1:5432/campusrelay
```

生产数据库和数据库角色需要单独创建。完成后确认 `user` 可读该文件：

```bash
sudo -u user test -r /etc/campus-relay/server.env && echo "env readable"
```

### 手动部署/验证

从 GitHub Actions 上传仓库内容到：

```text
user@ssh.orlyn.top:/opt/campus-relay/incoming/
```

rsync 时至少排除：

```text
.git/
.github/
.venv/
.pytest_cache/
.env
.env.*
```

随后通过原来的 SSH ProxyCommand 执行：

```bash
bash /opt/campus-relay/incoming/deploy/activate.sh <完整的40位Git提交SHA>
```

部署脚本会：

1. 验证提交 SHA 和上传目录结构；
2. 将代码复制到独立 release 目录；
3. 创建该版本的虚拟环境并安装 `requirements.txt`；
4. 从 `/etc/campus-relay/server.env` 读取生产环境变量；
5. 执行 `alembic upgrade head`；
6. 原子切换 `current` 软链接并重启 `campusrelay-api.service`；
7. 轮询 `/health`；失败时恢复上一个版本并重启服务。

数据库迁移本身不会自动反向回滚。新增迁移应保持向后兼容；涉及破坏性结构变更时，必须单独设计迁移与回滚方案。

### Nginx

本仓库的部署脚本不会自动改写 Nginx。确认后再为 `cr.orlyn.top` 单独配置 `/api/` 反向代理到 `http://127.0.0.1:8101`。不要修改现有站点配置来复用旧服务目录。

## 安全约定

- `.env`、生产数据库密码和 SSH 私钥不得提交到仓库。
- SSH 私钥只放在 GitHub Actions Repository Secret `SSH_PRIVATE_KEY`。
- 部署环境变量只保存在服务器 `/etc/campus-relay/server.env`。
- 部署目标是 `/opt/campus-relay`，不触碰旧站点 `/var/www/filehub`。
- 服务仅绑定 `127.0.0.1:8101`，不直接暴露公网端口。
