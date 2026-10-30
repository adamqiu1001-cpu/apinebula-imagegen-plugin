# Project instructions

This repository distributes the APINebula image skill as a plugin for both Codex (ChatGPT desktop app, Codex CLI) and Claude Code, on macOS and Windows. Keep installation and execution portable across both platforms and both hosts; use pathlib and Python subprocess argument lists, not shell-specific commands.

The shared skill lives in skills/apinebula-imagegen/ (SKILL.md, scripts/, references/, tests/). Plugin manifests: .claude-plugin/plugin.json and .codex-plugin/plugin.json; marketplaces: .claude-plugin/marketplace.json and .agents/plugins/marketplace.json. Bump `version` in both plugin manifests together for every release. SKILL.md must not depend on host-only substitutions without a fallback: Codex does not expand ${CLAUDE_SKILL_DIR}.

Two backends share one CLI: gpt-image models use the async image-tasks API; gemini-* models use the nanobanana group's synchronous generateContent API. Keys are per group. Scripts run through scripts/run.py, which keeps the virtual environment outside the plugin folder.

Keep API and storage credentials in the user configuration directory outside this repository. Logs and task files must not expose credentials, presigned URLs or provider response bodies. Preserve task IDs across interrupted downloads; never automatically retry a generation POST, including synchronous nanobanana requests.

Run offline tests with: python3 skills/apinebula-imagegen/scripts/run.py --self-test. Live generation and storage tests require appropriate user authorization and credentials. Distinguish offline checks from actual Windows, host or provider validation.

When changing dependencies, update requirements.txt and requirements.lock together. Keep the installer, README and platform setup examples consistent. Use main as the default branch; use codex/ branches and pull requests for subsequent changes.
