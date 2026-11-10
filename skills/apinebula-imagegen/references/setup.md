# 安装与配置

## 环境

Python 3.10+。macOS 用 `python3 --version` 检查；Windows 用 `py -3 --version` 或 `python --version`。没有合适版本时从 https://www.python.org/downloads/ 安装官方版本（Windows 安装时勾选 Python launcher）。macOS 自带的 Command Line Tools 版 `python3` 可能是 3.9：启动器会自动寻找已安装的 3.10+（如 python.org 版），找不到时报错提示。

本包不依赖 bash、curl、Homebrew 或 PowerShell 执行策略。

## 安装

### 作为插件（推荐）

仓库根目录同时带 Claude Code 和 Codex 的插件清单与 marketplace，两边装的是同一个 `skills/apinebula-imagegen/`。

Claude Code（终端或桌面版 Code 标签页共用设置）：

```text
claude plugin marketplace add adamqiu1001-cpu/apinebula-imagegen-plugin
claude plugin install apinebula-imagegen@apinebula
```

会话里也可以用 `/plugin marketplace add adamqiu1001-cpu/apinebula-imagegen-plugin` 和 `/plugin install apinebula-imagegen@apinebula`。更新：`claude plugin marketplace update apinebula` 后 `claude plugin update apinebula-imagegen@apinebula`。

Codex（ChatGPT 桌面版里的 Codex 和 Codex CLI 共用）：

```text
codex plugin marketplace add adamqiu1001-cpu/apinebula-imagegen-plugin
```

然后在 ChatGPT 桌面版的插件目录（或 CLI 里的 `/plugins`）找到 APINebula ImageGen 并安装。更新：`codex plugin marketplace upgrade apinebula`。安装或更新后新开对话。

### 作为普通技能

不想用插件时，在仓库根目录运行安装器，它只复制 `skills/apinebula-imagegen/` 并准备 Python 环境：

- macOS：`python3 install.py --host claude`（Claude Code，装到 `~/.claude/skills/`）或 `--host codex`（装到 `~/.agents/skills/`），`--host both` 两边都装。
- Windows PowerShell：`py -3 install.py --host both`。

已存在同名目录时停止，不覆盖个人修改。不要同时装插件版和普通技能版，否则会出现两个同名技能。

### Python 环境放在哪

技能目录里不放虚拟环境（插件更新会整体替换该目录）。启动器 `scripts/run.py` 第一次运行时建环境：

- macOS/Linux：`~/.local/share/apinebula-imagegen/venv-<版本>-<哈希>/`
- Windows：`%LOCALAPPDATA%\apinebula-imagegen\venv-<版本>-<哈希>\`

`requirements.lock` 变化时自动重建并删除旧环境。可用 `APINEBULA_IMAGEGEN_HOME` 改位置。`python3 run.py --setup` 只建环境；`python3 run.py --self-test` 跑离线测试（不需要 Key，不产生费用）。

## 配置 Key

每个分组一把 Key，由使用者在自己的终端隐藏输入，输入时不显示字符是正常的。下面的 `RUN` 是 `scripts/run.py` 的绝对路径；插件安装时让 Codex/Claude 告诉你实际路径。

macOS：

```sh
python3 "$RUN" configure-key --group nanobanana
python3 "$RUN" configure-key --group gpt-image
```

Windows PowerShell：

```powershell
py -3 "$RUN" configure-key --group nanobanana
```

| 分组 | 可用模型（举例，以 `models` 实际返回为准） | 文件 | 环境变量 |
| --- | --- | --- | --- |
| gpt-image | `gpt-image-2.5` | `~/.config/codex-imagegen/apinebula-api-key` | `APINEBULA_API_KEY` |
| nanobanana | `gemini-3.1-flash-image`、`gemini-3-pro-image-preview`、`gemini-2.5-flash-image` 等 | `~/.config/codex-imagegen/apinebula-nanobanana-key` | `APINEBULA_NANOBANANA_API_KEY` |

- 在 APINebula 控制台"令牌管理"创建对应分组的令牌，建议"模型可用列表"留空。不是 OpenAI、Google 或 Anthropic 的 Key。
- 只配了一把也能用：脚本默认选已配置的那个分组。两把都配时默认 `gpt-image`，可设环境变量 `APINEBULA_DEFAULT_MODEL=nanobanana` 改默认（也可以写完整模型名）。
- 模型名不用写死：`--model gpt-image`、`nanobanana`、`nanobanana-pro` 每次提交前按这把 Key 的模型列表解析成最新版本，中转站从 2.5 升到 2.6 时不用改任何东西。写了完整名字但已下线时，自动换成同系列最新版并在输出里注明；加 `--strict-model` 则改为报错。
- 检查 Key 属于哪个分组：`python3 "$RUN" models --group nanobanana`，输出该 Key 能用的模型列表和每个别名当前解析到的模型（`latest`），不建任务。
- macOS 文件权限 0600；Windows 先用 `icacls` 关闭继承、只授权当前用户再写入，失败则不写入。它是限制权限的本地文件，不是加密保险库；不要同步或分享该目录。
- 环境变量优先于文件。脚本不读取或修改 `OPENAI_API_KEY`，也不改 Codex/Claude 的模型配置。

## 本地参考图

**nanobanana：** 参考图以 base64 内联在请求里直接发给 APINebula，不经第三方，不需要配置。

**gpt-image：** 异步接口只收 HTTPS 链接，本地图默认临时上传到 tmpfiles.org（`expire=3600`，约一小时后由服务方删除；有效期内持有直链者可访问）。脚本随机化文件名，下载核验原图字节后才提交，不把 Key 发给图床，不在状态文件里记临时链接。授权要求见 SKILL.md。

需要私有存储时用 S3 兼容桶：

1. 创建**私有桶**，不开公开访问。
2. 创建仅能对 `codex-image-reference/` 前缀 PutObject、GetObject、DeleteObject 的专用凭证，不用管理员密钥。
3. 记下 S3 API endpoint、region、bucket、访问密钥。AWS 例：`https://s3.<region>.amazonaws.com`；R2 用控制台给的 S3 endpoint，region 一般是 `auto`。
4. 运行 `python3 "$RUN" configure-storage`，按提示填写（密钥隐藏输入）。配置存于 `~/.config/codex-imagegen/apinebula-storage.json`。
5. 提交时加 `--upload-provider s3`。签名链接 24 小时有效；任务结束后 `cleanup`，或给该前缀设 1–2 天自动删除规则。链接到期不等于对象被删除。

使用原始 PNG/JPEG/WebP，单张上限 20 MiB。

## 连续修改

```text
python3 "$RUN" submit --prompt-file "next-edit.txt" --out-dir "outputs/edit-v2" --continue
```

Codex 按 `CODEX_THREAD_ID`、Claude Code 按 `CLAUDE_CODE_SESSION_ID` 隔离聊天，记录在当前工作目录 `.apinebula/sessions/`，需要保留这个目录和任务文件。两者都没有的终端，每次加同一聊天专属的 `--session-file "work/chat-a.json"`。指定旧版本用 `--from-state "outputs/edit-v1/state.json"`。

## 手动执行示例

先写好 UTF-8 `prompt.txt`，在自己的工作目录执行：

```sh
python3 "$RUN" submit --prompt-file prompt.txt --out-dir output/demo-nb --model nanobanana --aspect-ratio 16:9 --image-size 2K
python3 "$RUN" submit --prompt-file prompt.txt --out-dir output/demo-gpt --model gpt-image
python3 "$RUN" status --state output/demo-gpt/state.json --wait 40 --interval 10
```

nanobanana 的 `submit` 直接返回图片；gpt-image 需要 `status` 查询并下载。结果保存为真实格式的 `image.png`、`image.jpg` 或 `image.webp`。

## 故障恢复

- 启动器报 Python 版本不足：安装 Python 3.10+ 后重试。报依赖安装失败：检查网络/代理后重试，或删除上面的环境目录让它重建。
- 401/403：Key 错误或分组不对。用 `models --group ...` 核对，用 `configure-key --group ...` 更新。环境变量已设置时先确认它没有覆盖新文件。
- gpt-image 提交 400 `api_error`：多半是 Key 属于 nanobanana 分组。用 `models --group gpt-image` 看列表里有没有 `gpt-image-*`。
- 报 `Model ... is not available to this key`：中转站下线了这个模型且同系列没有替代。按报错里列出的可用模型改 `--model`，或改用别名。
- 额度不足或模型不可用：到控制台确认，不要换成其他模型冒充成功。
- 代理/证书错误：保留 TLS 验证，先检查系统/企业代理及证书。确认能直连后可加 `--direct`，它不改全局网络设置。
- gpt-image `submission_outcome_unknown` 且无任务编号：不要再提交，查平台任务/用量记录。已有任务编号：重复 `status`。
- nanobanana `submission_outcome_unknown`：同步请求超时或断网，无法查询；查用量记录后再决定是否用新目录重试。
- 长时间排队（gpt-image）：保留 state.json 稍后继续，脚本不会在后台轮询。
