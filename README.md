# APINebula ImageGen

用 APINebula 生成和修改图片的插件，同一份技能可装进 **Codex**（ChatGPT 桌面版里的 Codex、Codex CLI）和 **Claude Code**（终端、Claude 桌面版的 Code 标签页），macOS 和 Windows 都能用。

| 模型 | APINebula 分组 | 方式 |
| --- | --- | --- |
| `gpt-image-2.5` | gpt-image | 异步任务：提交、查询、下载 |
| `gemini-3.1-flash-image`（默认）、`gemini-3-pro-image-preview` 等 | nanobanana | 同步，一次返回图片；可设比例和 1K/2K/4K |

连续修改（"再亮一点""改成白天"）自动沿用本聊天上一张成功出图，不用重复附图。

本仓库由只支持 Codex 和 gpt-image 的 [apinebula-imagegen](https://github.com/adamqiu1001-cpu/apinebula-imagegen) 改造而来。

## 安装

需要 Python 3.10+、网络，以及每人自己的 APINebula Key（每个分组一把）。

### Claude Code

```text
claude plugin marketplace add adamqiu1001-cpu/apinebula-imagegen-plugin
claude plugin install apinebula-imagegen@apinebula
```

或在会话里输入 `/plugin marketplace add adamqiu1001-cpu/apinebula-imagegen-plugin`，再 `/plugin install apinebula-imagegen@apinebula`。终端、桌面版 Code 标签页和 VS Code 共用这份安装。

### Codex / ChatGPT 桌面版

```text
codex plugin marketplace add adamqiu1001-cpu/apinebula-imagegen-plugin
```

然后在 ChatGPT 桌面版的插件目录（或 Codex CLI 的 `/plugins`）安装 **APINebula ImageGen**，新开对话。

### 不用插件

克隆仓库后运行 `python3 install.py --host both`（Windows：`py -3 install.py --host both`），把技能复制到 `~/.agents/skills/` 和 `~/.claude/skills/`。只要一边就用 `--host codex` 或 `--host claude`。不要和插件版同时装。

## 配置 Key

装好后第一次用时，让 Codex / Claude 给出带实际路径的命令，在**自己的终端**里运行，粘贴 Key（输入时不显示）：

```text
python3 "<技能目录>/scripts/run.py" configure-key --group nanobanana
python3 "<技能目录>/scripts/run.py" configure-key --group gpt-image
```

只配一把也行，脚本默认用已配置的分组。不确定 Key 属于哪个分组：`python3 "<技能目录>/scripts/run.py" models --group nanobanana`。Key 存在 `~/.config/codex-imagegen/`，不在仓库里，不要分享这个目录。

第一次运行会在用户目录建一个独立 Python 环境（约一分钟），插件更新不会删掉它。

## 用法

- `用 nanobanana 画一张赛博朋克城市夜景，16:9，2K。`
- `用 gpt-image-2.5 画一张雨后的森林，安静的水彩风格。`
- `把上一张改成白天。`（沿用上一张的模型和图片）
- `以我附上的图片为参考，换成夜景。`

本地参考图：nanobanana 直接内联发给 APINebula；gpt-image 的异步接口只收链接，会临时上传到 tmpfiles.org（一小时后由服务方删除，有效期内持有直链者可访问），上传前会先征得你同意。

详见 [安装与配置](skills/apinebula-imagegen/references/setup.md)、[Skill 指令](skills/apinebula-imagegen/SKILL.md)、[验证记录](验证记录.md)。

## 仓库结构

```text
.claude-plugin/plugin.json        Claude Code 插件清单
.claude-plugin/marketplace.json   Claude Code marketplace（name: apinebula）
.codex-plugin/plugin.json         Codex 插件清单（含界面信息）
.agents/plugins/marketplace.json  Codex marketplace（name: apinebula）
skills/apinebula-imagegen/        两边共用的技能：SKILL.md、scripts/、references/、tests/
install.py                        不用插件时的普通技能安装器
```

## 开发与测试

源码是 Python 脚本，不需要编译，也没有安装包产物。维护用 `.python-version` 里的 Python 3.13，日常使用支持 3.10+。

```text
python3 skills/apinebula-imagegen/scripts/run.py --setup       # 建 Python 环境（放在用户目录，不进仓库）
python3 skills/apinebula-imagegen/scripts/run.py --self-test   # 64 项离线测试，不需要 Key，不产生费用
claude plugin validate .                                       # 检查 Claude Code 插件清单
```

Windows 把 `python3` 换成 `py -3`。

- 发版时同时改 `.claude-plugin/plugin.json` 和 `.codex-plugin/plugin.json` 的 `version`，两边才会拉到更新。
- 依赖改动时 `requirements.txt` 和 `requirements.lock` 一起改；启动器检测到 lock 变化会自动重建环境。

## 多台电脑协作

- 每台电脑克隆同一个仓库，并执行一次 `git config --local pull.ff only`。
- 开始工作前先提交本地修改，再执行 `git pull --ff-only`。如果因为两边分叉而停下，先看清两边的改动，再决定合并还是变基。
- 功能修改放在分支上，通过 Pull Request 合入；`main` 始终保持可用。
- 换到另一台电脑前，先推送完成的工作。

以 [MIT 许可证](LICENSE) 发布。此仓库不包含个人密钥、生成图片或产品源代码。第三方 API 与对象存储的用量由各自服务商计费。
