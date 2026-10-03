<p align="center"><img src="apps/web/public/assets/parrot-logo.svg" width="68" alt="Dralo Studio" /></p>

# Dralo Studio · 短剧工坊

**从第一行剧本，到最后一幕。**

Dralo Studio 是在自己电脑上运行的 AI 短剧创作工作台。把故事、角色、场景、分镜、素材与剪辑放在一个创作空间里，使用你自己配置的模型服务推进作品。

[快速开始](docs/QUICKSTART.md) · [功能与架构](docs/ARCHITECTURE.md) · [使用指南](docs/USER_GUIDE.md) · [问题反馈](https://github.com/lnblfkj-del/Dralo-Studio/issues) · [官网](https://www.wal365.com)

## 这个仓库是什么

这里公开的是 **单机版源码**，采用 Apache-2.0 许可证。前端、本地 API、任务处理和本地 SQLite 数据库在你的电脑上运行。

- 不包含官方云端部署、内测运营后台、云端轻量客户端及其更新服务。
- 不提供单机版 Windows/macOS 安装包。源码启动后，通过本机浏览器使用。
- 不附带作者的模型账号、API Key、项目、生成素材或数据库。
- 这是首次公开的源码预览版，不代表每一项功能都已完成全平台验收。
- 模型生成仍会访问你配置的服务；本地运行不等于所有模型推理离线运行。

## 创作流程

| 阶段 | 你可以做什么 |
| --- | --- |
| 故事策划 | 上传或粘贴剧本，从灵感整理故事设定、角色关系与分集大纲 |
| 制作准备 | 管理角色、场景和项目资产，为作品选择视觉风格 |
| 分集与镜头 | 组织分集内容与镜头，调用自己的图像、视频及语音模型 |
| 自由画布 | 在无限画布中连接创作内容、素材和生成节点 |
| 成片整理 | 在多轨时间线上组织画面、声音与字幕，使用本地媒体处理能力 |

还有任务中心、智能体与 Skill 配置、模型渠道管理、市场探索和 3D 分镜导演台。部分能力依赖外部服务、额外运行时或自备素材，详情见[使用指南](docs/USER_GUIDE.md)。

## 快速开始

建议先准备 Node.js 22+、pnpm 和 **Python 3.12**。首次公开版在 Windows / Python 3.12 环境验证二进制依赖安装。FFmpeg/FFprobe 用于媒体处理，需要单独安装。

```sh
git clone https://github.com/lnblfkj-del/Dralo-Studio.git
cd Dralo-Studio
python -m venv .venv
```

然后按照[安装指南](docs/QUICKSTART.md)激活环境、安装依赖、生成本机配置、构建前端，启动 API 和 Worker。默认只监听 `127.0.0.1:8000`，不要直接把这套单机配置开放到公网。

## 技术组成

- **工作台**：React、TypeScript、Vite、Ant Design、TanStack Query、Zustand。
- **画布与编辑**：React Flow、Tiptap；3D 导演台使用 Three.js / React Three Fiber。
- **本地服务**：Python、FastAPI、SQLAlchemy、Alembic、SQLite。
- **任务与媒体**：独立 Worker、模型适配器、FFmpeg/FFprobe。

```text
apps/web/                 创作工作台
apps/server/              本地 API、任务与数据库初始化
apps/director-upstream/   3D 分镜导演台
packages/types/           共享类型
docs/                     公开使用文档
scripts/                  本机配置工具
```

## 数据与模型

首次初始化的模型渠道与模型列表为空，需要由使用者自行配置。内置视觉风格是提示词与示例，不代表附赠生成模型或模型额度。

项目元数据默认保存在 `data/app.db`，素材默认保存在 `storage/`。请连同 `.env` 中的加密密钥一起备份，且不要提交到 GitHub。供应商费用由你与供应商结算。

官方 MiniMax H3 等要求公网参考素材链接的服务，需要自行配置兼容的对象存储。本仓库不提供对象存储账号，也不自动开通付费服务。

## 资源与许可边界

源码许可证见 [LICENSE](LICENSE)。第三方代码保留原有许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

未经确认可再分发的 3D 模型、Mixamo 动作包及测试全景图不随源码公开。导演台保留程序化人物和自备模型导入；不要把他人的素材许可理解为本项目 Apache-2.0 许可。

## 一起完善

欢迎提交可复现的问题、改进建议和 Pull Request。提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按 [SECURITY.md](SECURITY.md)私下报告，不要在 Issue 中公开密钥或真实用户数据。

喜欢这个项目，可以点一个 **Star**，分享使用体验或帮助改进文档。真实的作品与反馈，比数字更能帮助项目成长。

---

**Dralo Studio** is a locally hosted, source-available AI short-drama workspace under Apache-2.0. Bring your own model providers and storage. This repository contains the standalone source edition, not the proprietary cloud service or its desktop client. See the Chinese guides above for setup, dependencies, limitations and third-party notices.
