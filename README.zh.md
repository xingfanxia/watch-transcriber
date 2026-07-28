# watch-transcriber

[English](README.md)

Apple Watch 语音转文字流水线（妙记转写约 2 元/小时）。手腕上录音，自动生成结构化笔记。

```
Apple Watch (语音备忘录) → iCloud 同步 → Mac 检测新 .m4a
  → 妙记（火山 Lark Minutes）语音识别 — 服务端说话人分离（Gemini/OpenAI 兜底）
    → 可插拔投递层 (Apple Notes、飞书、Obsidian、自定义)
```

## 为什么选这个方案

我们调研并否定了多个方案，最终选定这条路径。以下是我们的发现。

### 为什么不做自定义 Watch App？

一位做过自定义 watchOS 录音 App 的朋友分享了血泪教训：

- **watchOS 网络极不稳定。** 电量管理会激进地杀掉连接。从手表直接 `URLSession` 上传到第三方 API，理论上很美好，实际会各种失败。
- **CloudKit 做中转很恶心。** 链路变成：手表 → iPhone（代理）→ CloudKit → iPhone 下载 → 处理。音频从手表到处理，要走四跳。
- **30 秒分段带来新问题。** 短分段能扛住来电中断（录到一半电话来了），但一天录下来几百上千个文件，CloudKit 直接假死。
- **开发投入巨大。** watchOS 的限制每个版本都在微调，每次更新都得真机实测——模拟器根本跑不出真实行为。

> "如果只是录音、后面手动处理，手表体验还不错。但如果要自动化工作流，至少到目前我没发现什么好的工程方法。"

### 为什么不用 Apple 自带转写？

iOS 18+ 的语音备忘录自带转写功能，但是：

- **不支持中英混合（code-switching）。** 单语言模式——设备语言设中文，英文就乱码；设英文，中文就乱码。对双语用户完全不可用。
- **没有说话人识别。** 输出就是一整块文本，不分谁说的。
- **准确率约 80-90%**，而 Gemini 3 Pro 在中英混合基准测试中 MER 仅 7.2%。

### 为什么选语音备忘录 + launchd？

- **语音备忘录已经解决了所有难题。** 后台录音、来电恢复、无限时长、iCloud 同步——全靠 Apple 自家系统级权限，第三方 App 拿不到。
- **Action Button 可用。** Ultra 的操作按钮可以直接映射到语音备忘录，一键开录。
- **录音秒级同步。** 文件几秒内就出现在 Mac 的已知路径上。
- **launchd `WatchPaths`** 是 macOS 原生文件系统监听，零轮询、零耗电、零依赖。

### 语音识别：为什么默认选 妙记（火山 Lark Minutes）？

**默认走 妙记（`volc.lark.minutes`，`STT_PROVIDER=lark`）。** 它**一次调用就在服务端做完说话人分离**——不切块、不跨块缝合。在 5 段真实录音上验证（2026-06）：妙记对 4 段两人对话**每段都精准判 2 人**，而切块缝合的 Gemini/OpenAI 以及豆包 auc 模型全都虚高（3–5 人）；3.45 小时的长文件也一次吃下。难的从来不是转写，是「谁在说」——妙记把它当成服务端的一等任务，而不是缝合的事后补救。

妙记需要一个公网可下载的 FileURL，所以流水线会先把音频转成 16kHz 单声道小 mp3，上传到火山 TOS，给妙记一个预签名链接，转完再删掉。**TOS 建议用香港区域**——从中国大陆以外上传快得多（单线程 ~700KB/s vs 上海 ~10–30KB/s），妙记照样能取。需要 `VOLC_API_KEY` + `VOLC_TOS_*`（见 `.env.example`）。

为减少妙记按音频时长消耗的额度，安装 Senko 后，默认会在本地识别持续超过 10 秒的无人声间隙，并且只压缩发送给妙记的临时 mp3。安全约束是：每个间隙两端至少保留 3 秒；原始 m4a 永远不修改；妙记返回的时间戳会映射回原录音时间；Senko、ffmpeg 或时长校验任一步失败，都会自动回退到完整录音。设置 `LARK_TRIM_LONG_SILENCE=0` 可关闭，阈值见 `.env.example`。

**Gemini 3.5 Flash** 和 **OpenAI gpt-4o-transcribe-diarize** 作为兜底（`STT_PROVIDER=gemini|openai`），它们会自动切块长音频并跨块缝合说话人标签（详见下文）。我们最初对比中英混合音频的方案：

| 服务商 | 中英混合 MER | 每小时成本 | 说话人识别 |
|--------|-------------|-----------|-----------|
| **妙记（Lark Minutes）**—默认 | 良好（中文 + 混合） | 低 | **是—服务端，最佳** |
| Gemini 3 Pro | **7.2%**（最佳） | ~$0.50-2 | 否（需 prompt 引导） |
| Gemini 3.5 Flash | 良好 | ~$0.10 | 切块缝合 |
| OpenAI gpt-4o-transcribe-diarize | 英文 OK，中英混合较弱 | $0.45/hr | 是（原生） |
| Qwen3-ASR-Flash | 5.78% WER | ~$0.04 | 否 |
| OpenAI Whisper API | ~12%（单语言） | $0.36 | 否 |
| Deepgram Nova-3 | 不支持中文 | $0.31 | 是 |

两个兜底之间：一段 2 小时中英混合录音上和 `gpt-4o-transcribe-diarize` 完整对比，Gemini 在标点、code-switching（`ROI` 保留为 `ROI`，OpenAI 转成了 `RY`）、不会从中文语气词幻觉出英文片段这几方面都胜出——所以 Gemini 是首选兜底；OpenAI（`STT_PROVIDER=openai`）能捕捉更细颗粒度的语气词。

### 长音频处理（静音切分 + 并行）

Gemini 3 Flash 单次调用处理 >15 分钟音频时会**静默 summarize / 丢内容** — 在 2 小时文件上实测，单次调用的输出只到 01:22:00 就停了，并且把 71 分钟的对话塞进了一行 "turn"。本流水线会自动把长音频按静音边界切分（`ffmpeg silencedetect`），**并行**转写各 chunk（默认 8 并发）。

2 小时音频：切成 ~10 段（每段 8-15 分钟），并行转写 → 总耗时 ~60 秒（串行需要 ~10 分钟），而且**全程覆盖、无伪造内容**。各 chunk 的时间戳偏移回绝对时间后，stitching 层会：

- **丢弃残缺行**（`[X -` 没有 `]` 收尾 — Gemini 偶发垃圾输出）
- **clamp 单条 utterance 长度**（任何 > 2 分钟的单 turn 都是幻觉）
- **clamp 超出音频长度的时间戳**（尾部 silence 被 Gemini 当对话转录）
- **丢弃 Gemini compliance preamble 和 `（注：...）` 注释行**
- **按 start 时间过滤 chunk 重叠区 + 全局重排**（对 Gemini 偶发的非时序输出鲁棒）

摘要生成是一个独立的文本输入调用，所以避开了长输出 JSON 模式的脆弱性。chunk 级的 Gemini 503/429/5xx 瞬时错误会重试最多 3 次（指数 backoff），而不是让整个任务挂掉。

可调环境变量：`CHUNK_THRESHOLD_SEC` / `CHUNK_TARGET_SEC` / `CHUNK_MIN_SEC` / `CHUNK_MAX_SEC` / `CHUNK_PARALLELISM`（见 `.env.example`）。

## 踩坑指南

### TCC / 完全磁盘访问权限

语音备忘录的 `Group Container` 目录受 macOS TCC（透明度、同意与控制）保护。你的终端或 `launchd` 代理需要**完全磁盘访问权限**才能读取录音文件。

- **快速方案：** 系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 添加你的终端 App（Terminal.app、iTerm2 等）
- **正规方案：** 把脚本打包成签名的 `.app` bundle，单独给它 FDA 权限 — 避免给 `/bin/bash` 开后门

如果 watcher 运行了但始终找不到新文件，几乎可以肯定是这个原因。

### iCloud 优化存储

如果 Mac 存储空间紧张，macOS 可能会把录音保存为**零字节 stub**（已卸载到 iCloud）。文件出现在目录里但没有内容，需要等下载完成。

脚本已经会跳过小于 1KB 的文件，以及短于 `MIN_DURATION_SECONDS`（默认 60 秒，见 `.env.example`）的录音。如果想强制下载文件：

```bash
# 强制语音备忘录下载所有录音
open -g "/System/Applications/Voice Memos.app"
```

或者在系统设置 → Apple ID → iCloud 中关闭「优化 Mac 存储空间」。

### lark-cli appsecret 从钥匙串消失

如果飞书投递突然开始报 `keychain entry not found: lark-cli/appsecret:cli_a942426c0ab81cdd`，是 macOS 钥匙串里 lark-cli 的 OAuth 凭据被擦了（钥匙串重置、login keychain 重建、不完整重装都会触发）。`~/.lark-cli/config.json` 配置文件还在引用这个 app，但 secret 没了，连 `auth login` 都启动不了（device-flow OAuth 需要 appsecret 才能开始）。

恢复（需要之前保存过 appsecret，比如 1Password 里）：

```bash
printf '%s' '<APPSECRET>' | lark-cli config init \
  --app-id cli_a942426c0ab81cdd --app-secret-stdin --brand feishu
lark-cli auth login --recommend --no-wait --json   # → 用返回的 verification_url
lark-cli auth login --device-code <code>           # → 阻塞等待用户授权
lark-cli auth status                               # → 应该看到 tokenStatus: valid
```

要删除文档还需要 `drive:drive` scope，这个 scope 需要 Lark app 后台管理员审批：批准后重跑 `lark-cli auth login --scope "drive:drive offline_access" --no-wait --json`。

## 安装

### 前置条件

- macOS，登录 iCloud（与手表同一 Apple ID）
- Apple Watch，已安装语音备忘录（任何型号）
- 默认 **妙记** provider：火山引擎 `VOLC_API_KEY` + TOS 桶凭据（`VOLC_TOS_*`，建议香港区域）——见 `.env.example`。`pip install tos`。
- [Gemini API Key](https://aistudio.google.com/apikey)——始终需要（摘要阶段走 Gemini；也是 `gemini` 兜底 provider）。
- Python **3.12+**（系统自带的 `python3` 是 3.9，太老；用 `brew install python@3.12` 或 asdf 装）
- `ffmpeg` — 音频转换 + 长音频静音切分必需。`brew install ffmpeg`

### 安装步骤

```bash
git clone https://github.com/xingfanxia/watch-transcriber.git
cd watch-transcriber
cp .env.example .env
# 编辑 .env，填入 GEMINI_API_KEY 和投递配置
./setup.sh
```

### 配置投递目标

编辑 `.env` 选择转写结果去哪：

```bash
# 逗号分隔的目标列表
DELIVERY_TARGETS=file,apple_notes
```

可用的投递方式：

| 目标 | 说明 | 需要配置 |
|------|------|---------|
| `file` | 保存为 Markdown 文件 | `OUTPUT_DIR` |
| `local_archive` | 结构化 `data/YYYY-MM-DD/` 归档:单录音 `.md` + `daily.md` + `daily.html` 汇总 | `LOCAL_ARCHIVE_DIR`(默认 `./data`),`LOCAL_ARCHIVE_HTML=0` 跳过 HTML |
| `audio_archive` | AI 标题命名的 `.m4a` 拷贝,与归档笔记并排(`HHMMSS-<标题>.m4a`)—— Voice Memos 无重命名 API,这就是可浏览的录音库。不动原件、幂等。存量回填:`scripts/backfill/backfill_audio_archive.py` | 同 `LOCAL_ARCHIVE_DIR` |
| `manifest` | `data/manifest.json` —— 笔记↔音频↔原件 1:1 映射 + AI 话题分类(分类表在 `deliveries/manifest.py:CATEGORIES`),并生成 `data/by-topic/<分类>/` 符号链接视图。回填/分类:`scripts/backfill/backfill_manifest.py` | 同 `LOCAL_ARCHIVE_DIR` |
| `viewer` | 重新生成 `data/index.html` —— 自包含暗色档案 UI(搜索、分类筛选、转写时间戳点击跳播)。手动重建:`python3 -m deliveries.viewer` | 同 `LOCAL_ARCHIVE_DIR` |
| `archive_git` | 每条录音后自动 commit `data/` 仓库(笔记 + manifest;音频与生成物 gitignore,由 delivery 自举写入),有 remote 时自动 push。`data/` 是嵌套仓库 —— 本项目 GitHub repo 公开,个人数据绝不进那边;它自己的 remote 必须是私有 | `data/` 需已 `git init` |
| `r2_backup` | 归档 `.m4a` 上传到私有 Cloudflare R2 bucket(异地音频备份;≤10GB/月免费)。补传:`scripts/backfill/backfill_r2_audio.py` | 本机 `wrangler` OAuth 登录;`R2_BUCKET`(默认 `watch-transcriber-audio`) |
| `apple_notes` | 创建 Apple 备忘录 | `APPLE_NOTES_FOLDER` |
| `feishu` | 创建飞书文档(可选把所有权从 bot 转给你) | `FEISHU_FOLDER_TOKEN` 或 `FEISHU_WIKI_SPACE`;转移所有权需 `FEISHU_DOC_OWNER_ID` |
| `feishu_notify` | 飞书 IM 私信通知摘要 | `FEISHU_NOTIFY_USER_ID` |
| `obsidian_git` | 提交到 GitHub 仓库 | `OBSIDIAN_REPO`, `GITHUB_TOKEN` |
| `agent` | 委托给 `claude -p` | `AGENT_DELIVERY_PROMPT` |

**`DELIVERY_TARGETS` 顺序敏感**:`manifest` 依赖 `local_archive`/`audio_archive` 已落盘的输出,`viewer`/`archive_git` 又消费 manifest —— 保持 `local_archive, audio_archive, manifest, viewer, archive_git, r2_backup` 的相对顺序。

### 数据放哪(本 repo 是公开的 ⚠️)

`data/`(笔记、转写、音频、manifest)在这里被 gitignore,绝不允许 commit 进本 repo。备份三条腿:

| 内容 | 位置 | 方式 |
|---|---|---|
| 笔记 + manifest(带版本史) | **私有** `github.com/xingfanxia/watch-transcriber-data` | `data/` 内嵌套 git 仓库;`archive_git` 每条录音自动 commit + push |
| 音频(AI 标题拷贝) | **私有** Cloudflare R2 bucket `watch-transcriber-audio` | `r2_backup` 每条录音上传;补传 `scripts/backfill/backfill_r2_audio.py`(账本 `state/r2_uploaded.json`) |
| 原件 | Voice Memos + iCloud | pipeline 从不触碰 |

### 桌面 App(Tauri)

`desktop/` 是薄壳:环回 axum 服务器伺服 `data/`(带 HTTP Range,音频可拖进度),webview 加载同一个生成的 `index.html` —— 不存在第二份 viewer 实现。`cd desktop && npm run tauri dev` 运行,`npm run tauri build` 打包;`WATCH_TRANSCRIBER_DATA` 可覆盖档案位置。

- **说话人标注**(仅 app 内可编辑,走环回 API):详情页点说话人芯片给 `SPEAKER_N` 命名,可一键批量应用到当前筛选的全部录音;标注存进 `manifest.json` 的 `speakers` 字段,reprocess 不会丢,自动 commit+push 到私有笔记仓库,并驱动侧栏「说话人」筛选和转写显示。pipeline 重建档案后页面自动刷新。
- **新机器**:clone 本仓库直接开 app —— 档案缺失时显示引导页,跑一次 `python3 scripts/ops/restore_archive.py`(克隆私有笔记仓库、从 R2 拉回全部音频、seed 上传账本、重建 viewer)后自动进入。

### Agent 投递示例

`agent` 投递最灵活——它把任务委托给 Claude Code，可以调用任何已安装的 skill：

```bash
# 发到飞书文档
AGENT_DELIVERY_PROMPT=use lark-doc skill to create a feishu doc titled '{title}' with content: {content}

# 发到 Google Docs
AGENT_DELIVERY_PROMPT=use gws-docs skill to create a google doc titled '{title}' with content: {content}

# 发到 Slack
AGENT_DELIVERY_PROMPT=post to #voice-notes channel: {content}

# 发邮件
AGENT_DELIVERY_PROMPT=use gws-gmail-send to email me@example.com subject '{title}' body: {content}
```

### 手动测试

```bash
# 立即处理所有新录音
python3 transcribe.py

# 验证环境（API key、FDA 权限、投递依赖、LaunchAgent 状态）
python3 transcribe.py --doctor

# 不调用 Gemini、不投递，只预览要做什么
python3 transcribe.py --dry-run

# 重新处理某一天的所有录音（无视 processed-state）
python3 transcribe.py --reprocess 2026-05-13
python3 transcribe.py --reprocess 2026-05-13 --dry-run   # 只预览

# 临时换 OpenAI 提供商
STT_PROVIDER=openai python3 transcribe.py
```

### 设置 Action Button（Apple Watch Ultra）

设置 → 操作按钮 → App → 语音备忘录

一按开始录音，再按停止。

## 自定义投递

创建 `deliveries/your_target.py`，实现一个函数即可：

```python
def deliver(note: dict) -> bool:
    """
    note 包含：
      - title: str
      - transcript: str（带时间戳/说话人的原始转写）
      - summary: str
      - todos: list[str]
      - audio_path: str
      - timestamp: str（ISO 格式）
      - markdown: str（格式化后的 Markdown）
    """
    # 你的逻辑
    return True  # 成功
```

然后在 `.env` 的 `DELIVERY_TARGETS` 里加上 `your_target`。

## 工作原理

1. 在 Apple Watch 上用**语音备忘录**录音（或任何设备）
2. **iCloud 同步** `.m4a` 到 `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/`
3. **launchd 检测到**新文件（通过 `WatchPaths`）
4. **妙记（火山 Lark Minutes）** 执行语音识别 + 服务端说话人分离（或 Gemini/OpenAI 兜底），随后 Gemini 对文稿做摘要并生成标题
5. **投递层**将结构化笔记发送到你配置的目标——标题格式 `YYYY-MM-DD HH:MM 内容标题`，按名称排序即时间序

## 项目结构

```
watch-transcriber/
├── transcribe.py              # 主流水线
├── deliveries/
│   ├── __init__.py            # 投递路由
│   ├── file.py                # Markdown 文件输出
│   ├── local_archive.py       # 结构化 data/YYYY-MM-DD/ 归档（单录音 + 每日汇总 + HTML）
│   ├── audio_archive.py       # AI 标题命名的 .m4a 拷贝，与归档笔记并排
│   ├── manifest.py            # data/manifest.json 映射 + 分类表 + by-topic/ 视图
│   ├── viewer.py              # data/index.html 生成器（viewer_template.html）
│   ├── apple_notes.py         # Apple 备忘录（AppleScript）
│   ├── feishu.py              # 飞书文档（lark-cli）
│   ├── feishu_notify.py       # 飞书 bot 私信（附文档链接）
│   ├── obsidian_git.py        # GitHub 提交到 Obsidian 仓库
│   └── agent.py               # claude -p 委托（飞书、Slack 等）
├── scripts/backfill/          # 一次性运维:AI 标题回填 / 重排 / 飞书旧文档清理
├── setup.sh                   # 一键安装
├── com.watch-transcriber.plist # launchd 模板
├── .env.example               # 配置模板
└── state/                     # 已处理文件记录（gitignore）
```

## 贡献

这个项目被设计为**模块化、易于 fork**。每一层都是简单的、可替换的组件：

| 层 | 当前实现 | 想换？ |
|----|---------|-------|
| **录音** | Apple 语音备忘录 | 任何能将音频同步到已知目录的 App |
| **文件监听** | macOS `launchd WatchPaths` | `fswatch`、`inotifywait`（Linux）、轮询、云端触发 |
| **语音识别** | 妙记（火山 Lark Minutes）默认；Gemini 3.5 Flash / OpenAI 兜底 | Whisper、Qwen3-ASR、AssemblyAI、Deepgram — 在 `transcribe_and_summarize()` 加一个 provider 分支 |
| **投递** | 文件、Apple 备忘录、飞书、Obsidian、Agent | 在 `deliveries/` 里放一个带 `deliver(note)` 函数的 `.py` 文件 |

欢迎 PR：
- **新的语音识别引擎** — Whisper、Qwen3-ASR-Flash 等
- **新的投递目标** — Slack、Notion、微信、Telegram、邮件等
- **更好的文件监听** — `fswatch`、跨平台方案、Linux `inotify` 支持
- **更智能的摘要** — 自定义 prompt、话题提取、会议纪要模板

## 许可证

MIT
