# watch-transcriber

[English](README.md)

零成本 Apple Watch 语音转文字流水线。手腕上录音，自动生成结构化笔记。

```
Apple Watch (语音备忘录) → iCloud 同步 → Mac 检测新 .m4a
  → Gemini 3 Flash 语音识别 (多语言 + 说话人识别)
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

### 语音识别：为什么选 Gemini 3 Flash？

我们对比了中英混合音频的语音识别方案：

| 服务商 | 中英混合 MER | 每小时成本 | 说话人识别 |
|--------|-------------|-----------|-----------|
| Gemini 3 Pro | **7.2%**（最佳） | ~$0.50-2 | 否（需 prompt 引导） |
| Gemini 3 Flash | 良好 | ~$0.10 | 是 |
| Qwen3-ASR-Flash | 5.78% WER | ~$0.04 | 否 |
| OpenAI Whisper API | ~12%（单语言） | $0.36 | 否 |
| 豆包 ASR | 中文好，混合一般 | 未公开 | 未知 |
| Deepgram Nova-3 | 不支持中文 | $0.31 | 是 |

Gemini 3 Flash 在质量、多语言支持、说话人识别和成本之间取得了最佳平衡。

## 安装

### 前置条件

- macOS，登录 iCloud（与手表同一 Apple ID）
- Apple Watch，已安装语音备忘录（任何型号）
- [Gemini API Key](https://aistudio.google.com/apikey)（有免费额度）
- Python 3.10+（google-genai 首次运行自动安装）

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
| `apple_notes` | 创建 Apple 备忘录 | `APPLE_NOTES_FOLDER` |
| `feishu` | 创建飞书文档 | `FEISHU_FOLDER_TOKEN` 或 `FEISHU_WIKI_SPACE` |
| `feishu_notify` | 飞书 IM 私信通知摘要 | `FEISHU_NOTIFY_USER_ID` |
| `obsidian_git` | 提交到 GitHub 仓库 | `OBSIDIAN_REPO`, `GITHUB_TOKEN` |
| `agent` | 委托给 `claude -p` | `AGENT_DELIVERY_PROMPT` |

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
4. **Gemini 3 Flash** 执行语音识别（支持多语言 + 说话人识别）
5. **投递层**将结构化笔记发送到你配置的目标

## 项目结构

```
watch-transcriber/
├── transcribe.py              # 主流水线
├── deliveries/
│   ├── __init__.py            # 投递路由
│   ├── file.py                # Markdown 文件输出
│   ├── apple_notes.py         # Apple 备忘录（AppleScript）
│   ├── feishu.py              # 飞书文档（lark-cli）
│   ├── obsidian_git.py        # GitHub 提交到 Obsidian 仓库
│   └── agent.py               # claude -p 委托（飞书、Slack 等）
├── setup.sh                   # 一键安装
├── com.watch-transcriber.plist # launchd 模板
├── .env.example               # 配置模板
└── state/                     # 已处理文件记录（gitignore）
```

## 许可证

MIT
