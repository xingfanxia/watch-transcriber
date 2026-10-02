# watch-transcriber

[English](README.md)

跨平台录音/导入 + Apple Watch 语音转文字流水线（妙记转写约 2 元/小时），
最终都进入 **回音壁**（EchoWall）管理。

> **当前状态：** 已验证的默认入口仍是 Mac 上的 Voice Memos watcher。
> 当前 release 目标是 macOS、iOS、Android 的 App 内录音/导入；在
> [`docs/capture/PLAN.md`](docs/capture/PLAN.md) 的真机矩阵通过前，不会写成已经发布。
> Windows 11 完整能力排在下一个 release；当前 Windows 代码和 CI 产物只是未支持的
> 技术预览，不代表已经发布。

```
Apple Watch (语音备忘录) → iCloud 同步 → Mac 检测新 .m4a
  → 上传火山 TOS → 妙记 API 服务端转写 + 说话人分离
    → Gemini 生成标题/摘要 → 可插拔投递层 (Apple Notes、飞书、Obsidian、自定义)
      → 回音壁 EchoWall 桌面 App（浏览 · 播放 · 说话人标注 · 管理）

EchoWall 录音 / 导入 → App 内 Rust 持久队列 → 直接上传火山 TOS
  → 妙记 API → Gemini 标题/摘要 → App 内档案发布器

Apple Silicon macOS（用户明确安装）→ 本地 MOSS/Metal 或 Whisper/Metal
  → 本地匿名说话人 → 本地 Qwen3.8-27B 摘要
    → 已验证的本机档案；云备份可稍后同步
```

新的 App 路径不需要 EchoWall 自建处理服务。用户在明确的设置流程里提供自己的
provider/档案凭据，凭据只进入系统安全存储，不随 release 打包。Python watcher 在
迁移期仅作为已经验证的 Voice Memos 兼容入口保留。

MOSS 本地处理支持运行 macOS 14 及以上、至少 32 GiB 统一内存的 Apple Silicon Mac。
打开 **MOSS 本地**，明确点击安装转写、说话人和摘要模型。导入录音后，可分别选择
**MOSS 本地处理**、**Whisper 离线处理**或**云端处理**。
新录音的默认方式可单独保存，App 重启后仍有效，已有任务保持原选择。
尚未选择默认方式时，录音留在本机等待操作；本地模型不可用或偏好读取失败时，
不会自动下载或转为云端处理。旧 Whisper 偏好不会改成 MOSS，页面会提供明确的保存入口。
这些设置只影响 App 内新录音；原来的 Watch 录音同步到 Mac 后自动转写、总结和归档的后台流程保持自动运行。

当前实用质量标准是英文、中文和中英混合会议笔记达到与飞书/妙记相近的可用程度。
已有语料与完全本地的源码引擎验证支持这一目标；少数困难尾段仍可能明确失败，
原录音会保留，可重试或导出。这些验证不代表所有历史样本或最终安装发行版均已通过。
详见[当前就绪状态与限制](docs/capture/PLAN.md)。
当前 macOS、Android 与 iOS 本地构建的路径、哈希和安装限制见
[9 月 6 日交付说明](docs/capture/evidence/practical-closeout-2026-09-06.md)。

**0.3.0 预览版已发布：**[下载 Mac 和 Android 安装包](https://github.com/xingfanxia/watch-transcriber/releases/tag/v0.3.0)。现有 iOS 内部测试人员可在 TestFlight 选择 **0.3.0（3）**。手机实际测试仍待完成。

## 回音壁 EchoWall —— 桌面客户端

流水线的产出不是一堆再也不会打开的 markdown —— 仓库自带一个桌面 App（`desktop/`，Tauri v2），把本地档案变成可浏览、可播放、可管理的语音金库：

| 摘要 —— 双语摘要、要点、待办 | 转写 —— 说话人彩色标注、点时间戳跳播 |
|---|---|
| ![概览:摘要 tab,双语摘要与说话人芯片](docs/screenshots/01-overview.png) | ![转写 tab,说话人配色与可点击时间戳](docs/screenshots/02-transcript.png) |
| **附注 —— Markdown 应用内渲染** | **说话人 —— 快速标注 + 批量应用** |
| ![附注 tab,渲染含表格与引用的 markdown 分析](docs/screenshots/03-attachments.png) | ![说话人选择器,快选与批量应用](docs/screenshots/04-speaker-tagging.png) |

*截图内容均为虚构演示数据。*

- **暗色档案 UI** —— AI 标题、双语摘要、要点、完整分说话人转写；搜索、话题 + 说话人筛选、按天汇总；详情页分 摘要/附注/转写 三个 tab（快捷键 1/2/3）；点转写里任意时间戳，音频直接跳到那一刻。pipeline 投递新录音后页面自动同步。
- **说话人标注** —— 点芯片给 `SPEAKER_N` 命名，可批量应用到当前筛选，支持自选人物颜色；每行显示层叠的头像堆。标注存进 `manifest.json` 的 `speakers` 字段，reprocess 不丢，自动 commit+push 到私有笔记仓库，并回写进笔记文件的转写标签（`scripts/ops/apply_speakers.py`，借 `speakers_applied` 可逆）。
- **Markdown 附注** —— 每条录音可粘贴或选择 `.md`/`.txt`（对话的 AI 分析、会议背景，随便什么）；存在 `data/<日期>/<HHMMSS>-attachments/`，记入 manifest，app 内渲染。`scripts/ops/import_gpt_thread.py <export.json>` 批量导入 ChatGPT 导出（含全部分支，兼容三代历史上传文件名）：给每条录音挂上对应分析，并自动提取「SPEAKER_N 是谁」打标（绝不覆盖手工标注）。幂等可重跑。
- **安全删除** —— 两步确认的「删除」按钮通过 Rust 原生 archive CAS 删除笔记、App 所有的音频、附注和 manifest 条目，并发布 recording tombstone，避免旧任务复活录音。没有 App 所有权元数据的历史 R2 对象会要求第二次明确确认，只做 partial delete，并清楚显示远端旧音频仍保留。Voice Memos 原件和 Apple Notes/飞书分身有意不动；`scripts/ops/delete_recording.py` 仅保留为显式维护 CLI。
- **可恢复的录音控制** —— 处理队列重启后自动恢复，提供重试、取消、导出原录音、复用 transcript 的重新处理，以及二次确认后的本机副本丢弃。桌面窗口关闭不会停止录音；菜单栏显示来源与计时，可暂停/继续/停止，退出前会先确认并安全关闭当前分段。
- **Apple Silicon 可选端到端完全离线处理** —— 明确安装 MOSS 本地模型包，也可继续使用独立的 Whisper 备选路线。两者均提供匿名说话人、Qwen3.8-27B `UD-Q4_K_XL` 双语摘要和已验证的本机档案；安装前会显示下载量和硬件要求。Rust 管理持久任务与恢复，受限的一次性 worker 负责模型处理。本地完成不会请求 TOS、妙记、Gemini、GitHub 或 R2；云备份需另行明确选择。
- **本地优先，数据归你** —— 一个 Rust + Tauri App 负责录音、导入、持久处理、档案发布和带 capability path 的 HTTP Range 传输。webview 只渲染 App 内编译的可信 viewer。没有回音壁自建云端、账号或 processing server；数据只去你自己配置的 TOS、妙记、Gemini、GitHub 和 R2。源码开发时用 `WATCH_TRANSCRIBER_DATA` 覆盖 Rust App 档案位置；旧 Python watcher 使用 `LOCAL_ARCHIVE_DIR`。
- **新机器几分钟就位** —— 独立安装版把档案放进系统 app-data。首次启动既可不配置任何凭据、直接创建完全本机档案，也可通过 write-only 原生命令配置 GitHub/R2 私有仓库/bucket 并同步；空档案也能正常进入 App，不会卡在 setup。App 不要求 clone 仓库或运行 Python restore。

```bash
cd desktop
npm install
npm run tauri:dev:macos    # 对 ../data 运行，并构建本地模型 sidecar
npm run tauri:build:macos  # 本地打包；发行签名由 CI 凭据完成
```

不想自己构建：直接从 [**GitHub Releases**](https://github.com/xingfanxia/watch-transcriber/releases) 下载最新的 `EchoWall_*_universal.dmg`（Apple Silicon + Intel 通用）。每次打 `v*` tag，CI 自动构建发版（`.github/workflows/release.yml`）。

> Release 构建带 **Developer ID 签名并通过 Apple 公证**。CI 先验证通用 App，
> 再单独公证并 staple 最终 DMG，之后才允许上传。

## 移动端

回音壁同样跑在 **iOS 和 Android** 上，仍复用同一个 Tauri 壳和同一份生成页面。
已经验证的是档案浏览/同步。新增路径是用户主动开启的 App 内麦克风录音和原生文件/分享导入：
Android 已有 foreground service 录音与 SAF/分享入口，iOS 已有后台 AVFAudio 录音和
Voice Memos/Files Share Extension。当前已作为预览功能发布，手机上的真实使用与后台录音测试仍待完成。
说话人、附注、删除等档案编辑仍留在桌面端。
应用内的 loopback 只是带一次性 capability path 和同源写校验的媒体传输层，不承担 processing。viewer 模板和 markdown 运行时编译进 App，不会执行从档案仓库同步下来的 HTML/JavaScript。

| 时间流列表 + 同步状态 | 详情:tab、播放器、离线 pin | 首次运行 token 配置 | 手动深浅色切换 |
|---|---|---|---|
| ![移动端列表:按日分组与同步状态胶囊](docs/screenshots/05-mobile-list.png) | ![移动端详情:摘要 tab、底部播放器与 pin 按钮](docs/screenshots/06-mobile-detail.png) | ![token 配置页:GitHub 与 R2 凭据](docs/screenshots/07-mobile-setup.png) | ![浅色主题下的移动端列表](docs/screenshots/08-mobile-light.png) |

*截图均为虚构演示数据。*

- **直拉同步** —— 启动时(以及点同步胶囊时)下载笔记仓库 tarball 覆盖进 app 沙盒，再由 App 内置的可信 viewer 模板从经验证的 manifest/笔记生成界面。同步状态:同步中 / ✓ 已同步 / 同步失败 / 离线 / token 已过期。
- **音频:流式 + 缓存 + pin** —— 播放走 R2 的 HTTP Range(可拖进度),500MB LRU 磁盘缓存让重播走本地,播放器上的 ↓ 把单条录音固定到离线。离线时:笔记永远可看,已 pin 的音频照常播,未缓存的显示 离线未缓存。
- **token 存平台安全存储** —— iOS Keychain / Android Keystore,不落明文文件,更不进这个仓库。
- **恢复导出走原生通道** —— iOS 导出已校验的非空文档，Android 写入用户选择的 SAF `content://` 目标；两端都会重新打开结果核对大小/hash，取消或失败不会删除 App 内副本。

**token 配置**(首次运行需要两份仅限你自己档案的凭据，并明确填写对应的私有仓库和 bucket):

1. **GitHub fine-grained PAT** —— 仓库范围只选私有笔记仓库。仅浏览使用 Contents: Read-only；要让 App 内录音/导入发布档案，需要 Contents: Read and write。最长有效期 1 年，记得设轮换提醒。
2. **R2 API token** —— 范围只限定音频 bucket。仅播放使用 Object Read only；要让 App 发布录音，需要 Object Read & Write。创建后页面会给出 **Access Key ID** 和 **Secret Access Key**，**Account ID** 在 R2 概览页。

公开构建不包含任何共享凭据。只读 token 仍可用于纯浏览；在当前安装没有用户自己的写权限前，回音壁必须把处理能力显示为未配置。

**安装**:现有 iOS 内部测试人员可在 TestFlight 选择 0.3.0（3）· Android 从 [GitHub Releases](https://github.com/xingfanxia/watch-transcriber/releases) 下载签名的 `EchoWall_*_universal.apk` 侧载(与 dmg 同一条 CI 构建签名)。

从源码构建(需要 Xcode / Android SDK+NDK):

```bash
cd desktop
npm run tauri ios dev      # iOS 模拟器(先手动 boot)
npm run tauri android dev  # Android 模拟器
npm run tauri ios build -- --export-method app-store-connect   # App Store ipa
npm run tauri android build -- --target aarch64 x86_64 --apk # 签名 64 位 APK
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

### 为什么仍保留语音备忘录 + launchd？

- **它是兼容兜底，不是新的 processing architecture。** 语音备忘录已经处理了后台录音、
  来电中断、长文件和 iCloud 同步；App 内录音通过同等可靠性验证前，现有 watcher 一直保留。
- **Action Button 可用。** Ultra 的操作按钮可以直接映射到语音备忘录，一键开录。
- **录音秒级同步。** 文件几秒内就出现在 Mac 的已知路径上。
- **launchd `WatchPaths`** 只负责这条旧的 Mac 入口。iPhone App 无法扫描 Voice Memos
  私有容器；手机上要么用「分享 → EchoWall」，要么直接在 EchoWall 内录音。

### 语音识别：为什么默认选 妙记（火山 Lark Minutes）？

**默认走 妙记（`volc.lark.minutes`，`STT_PROVIDER=lark`）。** 它**一次调用就在服务端做完说话人分离**——不切块、不跨块缝合。在 5 段真实录音上验证（2026-06）：妙记对 4 段两人对话**每段都精准判 2 人**，而切块缝合的 Gemini/OpenAI 以及豆包 auc 模型全都虚高（3–5 人）；3.45 小时的长文件也一次吃下。难的从来不是转写，是「谁在说」——妙记把它当成服务端的一等任务，而不是缝合的事后补救。

当前已经验证的旧 watcher 主链路很简单：

```text
检测到新 .m4a → 上传压缩副本到火山 TOS → 调用妙记 API
```

妙记需要一个公网可下载的 FileURL。旧 Python watcher 会把完整录音转换成 16kHz 单声道小 MP3，放进 TOS，再把预签名链接交给妙记，任务结束后删除临时对象。新的 App 路径不调用 Python 或 ffmpeg，由 Rust/Symphonia 完整解码校验 `.m4a`、`.mp3`、`.wav`，再用匹配的后缀和 MIME 上传，之后走同一条妙记 → Gemini 文本摘要链。两条默认路径都不运行 Senko、pyannote、本地说话人分离或音频切块（watcher 当前为 `LARK_TRIM_LONG_SILENCE=0`）。

**TOS 建议用香港区域**——从中国大陆以外上传快得多（单线程 ~700KB/s vs 上海 ~10–30KB/s），妙记照样能取。需要 `VOLC_API_KEY` + `VOLC_TOS_*`（见 `.env.example`）。

仓库仍保留早期的 **Gemini 3.5 Flash** 和 **OpenAI gpt-4o-transcribe-diarize** 备用 provider（`STT_PROVIDER=gemini|openai`）。相关的切块和本地说话人分离代码不属于默认妙记链路。我们最初对比中英混合音频的方案：

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

### 长音频处理（仅 Gemini/OpenAI 备用链路）

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

如果飞书投递突然开始报 `keychain entry not found: lark-cli/appsecret:<YOUR_LARK_APP_ID>`，是 macOS 钥匙串里 lark-cli 的 OAuth 凭据被擦了（钥匙串重置、login keychain 重建、不完整重装都会触发）。`~/.lark-cli/config.json` 配置文件还在引用这个 app，但 secret 没了，连 `auth login` 都启动不了（device-flow OAuth 需要 appsecret 才能开始）。

恢复（需要之前保存过 appsecret，比如 1Password 里）：

```bash
printf '%s' '<APPSECRET>' | lark-cli config init \
  --app-id <YOUR_LARK_APP_ID> --app-secret-stdin --brand feishu
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
- `ffmpeg` — 仅旧 Python watcher 和备用 provider 需要；独立 App 路径不会调用。`brew install ffmpeg`

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

### 摘要语言

笔记默认包含英文 + 中文的摘要和要点。在 `.env` 中把 `SUMMARY_LANGUAGES` 设为以逗号分隔的 ISO 639-1 语言代码（按显示顺序）即可更改：`en` 仅英文，`en,es` 为英文 + 西班牙文，以此类推。转写始终保持说话时的原语言。该设置作用于 Python watcher 的各投递目标和归档页面；EchoWall App 自己录制的录音仍使用内置的英文 + 中文摘要。

### 数据放哪(本 repo 是公开的 ⚠️)

`data/`(笔记、转写、音频、manifest)在这里被 gitignore,绝不允许 commit 进本 repo。备份三条腿:

| 内容 | 位置 | 方式 |
|---|---|---|
| 笔记 + manifest(带版本史) | **私有** `github.com/xingfanxia/watch-transcriber-data` | `data/` 内嵌套 git 仓库;`archive_git` 每条录音自动 commit + push |
| 音频(AI 标题拷贝) | **私有** Cloudflare R2 bucket `watch-transcriber-audio` | `r2_backup` 每条录音上传;补传 `scripts/backfill/backfill_r2_audio.py`(账本 `state/r2_uploaded.json`) |
| 原件 | Voice Memos + iCloud | pipeline 从不触碰 |

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
│   ├── archive_git.py         # 嵌套私有 data/ 仓库自动 commit+push
│   ├── r2_backup.py           # 每条录音音频上传私有 R2 bucket
│   ├── apple_notes.py         # Apple 备忘录（AppleScript）
│   ├── feishu.py              # 飞书文档（lark-cli）
│   ├── feishu_notify.py       # 飞书 bot 私信（附文档链接）
│   ├── obsidian_git.py        # GitHub 提交到 Obsidian 仓库
│   └── agent.py               # claude -p 委托（飞书、Slack 等）
├── desktop/                   # 回音壁 EchoWall —— Tauri 壳（axum 环回服务 + 管理 API）
├── scripts/backfill/          # 幂等回填:音频拷贝 / manifest+分类 / R2 同步
├── scripts/ops/               # apply_speakers / delete_recording / import_gpt_thread / restore_archive
├── tests/                     # pytest 测试套件（命名、投递、manifest、viewer、删除）
├── setup.sh                   # 一键安装
├── com.watch-transcriber.plist # launchd 模板
├── .env.example               # 配置模板
└── state/                     # 已处理文件记录 + R2 账本（gitignore）
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
