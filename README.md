<p align="center"><img src="apps/web/public/assets/parrot-logo.svg" width="68" alt="Dralo Studio" /></p>

# Dralo Studio · 短剧工坊

**从第一行剧本，到最后一幕。**

Dralo Studio（短剧工坊）是一套**开源 AI 短剧创作工作台**，面向短剧、AI 漫剧与独立影像创作，将**剧本创作、角色与场景、分集分镜、无限画布、AI 图像与视频生成、多轨剪辑**连接为一条完整创作流程。

在自己的电脑上运行，项目和素材由你掌握，模型渠道由你配置。它不仅是一张生图画布，也不仅是剧本生成器：从一个故事的想法开始，逐步建立能够持续迭代的作品。

**Open-source AI short-drama studio · Screenwriting · Infinite canvas · Storyboard · AI video workflows**

[官网](https://www.wal365.com) · [快速开始](docs/QUICKSTART.md) · [详细功能](docs/FEATURES.md) · [功能与架构](docs/ARCHITECTURE.md) · [问题反馈](https://github.com/lnblfkj-del/Dralo-Studio/issues)

![Apache-2.0 开源许可证](https://img.shields.io/badge/license-Apache--2.0-227765)
![单机源码发行](https://img.shields.io/badge/edition-standalone-303238)

## 一条完整的短剧创作流程

![Dralo Studio AI 短剧创作流程：剧本策划、角色场景、分集分镜、无限画布与多轨成片](docs/assets/readme/creative-workflow.svg)

你可以从一段灵感、一份已有剧本或一个空项目开始，逐步整理故事、准备资产、制作镜头，再组织成片。下面的图片是**功能结构示意**，不冒充产品截图、实测生成结果或已完成的作品。

## 这个仓库是什么

这里公开的是 **单机版源码**，采用 Apache-2.0 许可证。前端、本地 API、任务处理和本地 SQLite 数据库在你的电脑上运行。

- 不包含官方云端部署、内测运营后台、云端轻量客户端及其更新服务。
- 不提供单机版 Windows/macOS 安装包。源码启动后，通过本机浏览器使用。
- 不附带作者的模型账号、API Key、项目、生成素材或数据库。
- 当前为 R3 单机源码内测版，不代表所有模型与平台均已验收。旧部署请先阅读 [切换说明](docs/UPGRADING.md)，不能直接覆盖旧数据库。
- 模型生成仍会访问你配置的服务；本地运行不等于所有模型推理离线运行。

## 核心功能

### 剧本创作：先让故事成立

上传或粘贴 TXT、Markdown、DOCX 剧本，也可以从灵感开始。围绕故事设定、人物关系与分集大纲组织创作，再推进到分集正文和制作准备，让故事材料与项目始终连接在一起。

### 无限画布：把素材与生成连接起来

在自由画布中组织提示词、人物、场景、图片、视频与声音节点，通过连线表达引用与生成关系。视频节点可以区分参考图、首帧和尾帧；素材可以在画布和项目中复用，而不必每次重新上传。

### 分集与分镜：让文字走向镜头

围绕分集组织片段与镜头，连接角色、场景和项目素材。3D 分镜导演台提供摆位与机位预演参考；公开版采用程序化人物，自备模型需要合法的素材授权。

### 多模型与智能体：保留创作选择

自行配置模型渠道、模型参数、智能体与 Skill，让不同任务使用适合自己的服务。支持的适配包括 OpenAI 兼容接口、Google Gemini 和 Anthropic 等；实际模型能力与接口限制以服务商及当前适配实现为准，不附带作者账号或免费额度。

### 资产、任务与多轨：把创作继续下去

在资产中心整理素材，在任务中心检查耗时操作的状态与结果。画布基础媒体处理支持裁剪、旋转、截取片段、视频抽帧、提取音轨与音画合成；多轨编辑用于组织画面、声音和字幕，媒体处理需要本机 FFmpeg/FFprobe。

## 功能图解

![AI 剧本创作示意：从故事设定到人物关系、分集大纲与剧本正文](docs/assets/readme/story-development.svg)

![无限画布工作流示意：提示词、角色与场景参考连接图像及视频生成节点](docs/assets/readme/infinite-canvas.svg)

![短剧分镜与成片示意：镜头、声音及字幕在多轨时间线中组织](docs/assets/readme/storyboard-timeline.svg)

## 你可以如何使用

| 阶段 | 你可以做什么 |
| --- | --- |
| 故事策划 | 上传或粘贴剧本，从灵感整理故事设定、角色关系与分集大纲 |
| 制作准备 | 管理角色、场景和项目资产，为作品选择视觉风格 |
| 分集与镜头 | 组织分集内容与镜头，调用自己的图像、视频及语音模型 |
| 自由画布 | 在无限画布中连接创作内容、素材和生成节点 |
| 成片整理 | 在多轨时间线上组织画面、声音与字幕，使用本地媒体处理能力 |

还有任务中心、智能体与 Skill 配置、模型渠道管理、市场探索和 3D 分镜导演台。部分能力依赖外部服务、额外运行时或自备素材，详情见[使用指南](docs/USER_GUIDE.md)。

适合已有剧本的短剧创作者、探索视觉方案的 AI 漫剧作者，以及需要自备模型、自主管理素材的独立创作团队。它提供创作工具，不承诺“一键生成爆款”或不经人工检查就完成商业交付。

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

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [安装与启动](docs/QUICKSTART.md) | 环境要求、依赖安装、本机配置、API 与 Worker |
| [详细功能介绍](docs/FEATURES.md) | 剧本、无限画布、分镜、模型、智能体、资产及剪辑 |
| [使用指南](docs/USER_GUIDE.md) | 首次使用、模型设置、存储与对象存储、备份 |
| [系统架构](docs/ARCHITECTURE.md) | 前端、本地服务、Worker、数据库及产品边界 |
| [版本说明](RELEASE_NOTES.md) | 已发布内容、验证范围与已知限制 |
| [贡献指南](CONTRIBUTING.md) | Issue、Pull Request 与测试要求 |
| [第三方许可](THIRD_PARTY_NOTICES.md) | 第三方源码及资源的许可边界 |

## 联系与交流

官网：[www.wal365.com](https://www.wal365.com)

欢迎交流短剧创作、无限画布工作流、使用反馈，以及定制开发、私有化部署与合作需求。添加维护者微信时请备注 **Dralo Studio**。

<table>
  <tr><th>维护者微信</th><th>微信交流群</th></tr>
  <tr>
    <td align="center"><a href="docs/assets/community/wechat-contact.png"><img src="docs/assets/community/wechat-contact.png" width="220" alt="Dralo Studio 维护者微信联系二维码" /></a></td>
    <td align="center"><a href="docs/assets/community/wechat-group.png"><img src="docs/assets/community/wechat-group.png" width="220" alt="Dralo Studio 短剧与无限画布微信交流群二维码" /></a></td>
  </tr>
  <tr><td align="center">创作交流 · 定制与合作</td><td align="center">使用分享 · 工作流讨论</td></tr>
</table>

扫码前可点击图片查看原图。微信群二维码可能过期；如果无法入群，请通过左侧微信联系维护者。Bug 建议同时提交到 [GitHub Issues](https://github.com/lnblfkj-del/Dralo-Studio/issues)，便于追踪与修复；不要在群内或公开 Issue 发送密钥、密码或私有剧本。

---

## English Overview

**Dralo Studio** is an **open-source AI short-drama creation workspace** for screenwriting, story development, character and scene management, storyboard planning, infinite-canvas workflows, AI image/video generation, and multitrack editing.

Run the standalone source on your own computer, bring your own model providers, and keep control of project data and local assets. Its workflow connects scripts, episodes, shots and media rather than treating every generation as an isolated result.

The stack uses React, TypeScript, Vite, FastAPI, SQLite and a separate job worker. Media processing requires FFmpeg/FFprobe. This public repository is licensed under Apache-2.0; proprietary cloud operations and the cloud desktop client are not included. No model credentials, paid API credits or unlicensed third-party asset packs are bundled.

Start with [Quick Start](docs/QUICKSTART.md), explore the [Feature Guide](docs/FEATURES.md), visit [wal365.com](https://www.wal365.com), or report reproducible problems in [Issues](https://github.com/lnblfkj-del/Dralo-Studio/issues). The guides are currently primarily in Chinese.
