# 安装与启动

本指南用于全新的本地源码安装，不用于覆盖云端服务或直接升级旧业务数据库。

## 1. 环境

| 依赖 | 要求 |
| --- | --- |
| Node.js | 22 或更高版本 |
| pnpm | 11.x；使用仓库 `packageManager` 指定的版本 |
| Python | 推荐 3.12；首次公开版验证环境为 Windows / Python 3.12.14 |
| FFmpeg / FFprobe | 媒体处理需要，安装后应能在终端直接运行 |

首次公开源码在 Windows 环境检查。macOS/Linux 的源码兼容性不等于已经完成安装包或全部交互验收。

## 2. Python 环境

在仓库根目录执行：

```sh
python -m venv .venv
```

Windows PowerShell 激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux 激活：

```sh
source .venv/bin/activate
```

安装依赖：

```sh
python -m pip install --upgrade pip
python -m pip install --only-binary=:all: -r apps/server/requirements.lock.txt
```

`requirements.lock.txt` 固定本次验证过的直接及传递依赖。若提示某项依赖没有对应 wheel，请先确认 Python 版本、系统与 CPU 架构，不要直接去掉锁定版本或改用来源不明的包。

## 3. 本机配置

```sh
python scripts/init_local_config.py
```

按提示设置本机管理员密码。工具仅创建新的 `.env`，生成独立 JWT 密钥与模型配置加密密钥，不覆盖已有配置。首次登录账号为 `admin`，密码是你刚才输入的密码。

`.env` 不应提交、公开或发给他人。丢失模型加密密钥可能导致已有供应商凭据无法解密。

若 FFmpeg/FFprobe 不在 PATH 中，在 `.env` 中填写 `FFMPEG_PATH` 和 `FFPROBE_PATH` 的绝对可执行文件路径。API 与 Worker 必须使用同一配置；音频提交会预检解码工具，缺失时不会发起收费生成。

## 4. 构建前端

```sh
pnpm install --frozen-lockfile
pnpm --dir apps/director-upstream install --frozen-lockfile
pnpm build
```

3D 导演台是独立的依赖工程，不能只安装根目录依赖。`pnpm build` 先构建导演台，再构建工作台。

## 5. 初始化并运行

激活 Python 环境后，在第一个终端：

```sh
cd apps/server
python -m app.standalone_initialize
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

R3 初始化使用新基线 `ds_r3_0001`，只接纳空的新库或匹配的新基线数据库。此前 `ds_0001` 和未知旧库不会被原地转换，普通启动不会删除数据库。旧部署请先阅读 [升级说明](UPGRADING.md)，不要改迁移编号或直接删原库绕过检查。

初始化同时安装并校验 99 张内置风格图片，不调用模型、不产生费用。文件缺失或校验失败时应停止启动，检查候选源码与素材目录权限；不要忽略错误。账号首次启动时建立风格图片引用，不覆盖已选图片或自定义风格内容。

在另一个激活了同一 Python 环境的终端：

```sh
cd apps/server
python -m app.jobs.worker
```

API 接收操作，Worker 消费生成与处理任务，两者都需要运行。只进行媒体处理测试时可用 `python -m app.jobs.worker --local-media-only`，避免消费收费模型生成任务。

访问 <http://127.0.0.1:8000>，登录后在模型管理中添加你自己的模型渠道。

## 6. 开发模式

API 仍监听 `127.0.0.1:8000`；根目录运行 `pnpm dev:web`，前端地址为 <http://127.0.0.1:5173>。开发服务器代理 `/api` 到本机 API。3D 导演台修改后需要重新构建。

## 常见问题

- **生成任务一直等待**：确认 Worker 在运行，模型渠道配置完整，检查任务中心与本机日志。
- **浏览器提示连接失败**：确认 API 已启动，端口未被占用。
- **媒体处理失败**：确认 `ffmpeg -version` 和 `ffprobe -version` 可以运行，素材格式与磁盘空间满足要求。
- **需要公网参考链接**：自行配置对象存储；仅在本机可访问的文件地址不能充当公网 URL。
- **更新源码**：停止 API/Worker，备份数据库、素材和 `.env`，阅读版本说明。首次公开基线不承诺直接迁入此前内部版本数据库。

本指南没有把服务开放到公网的步骤。多租户服务的权限、隔离、配额与运维不属于本仓库的单机启动方案。
