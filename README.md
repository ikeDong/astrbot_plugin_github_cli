# astrbot_plugin_github_cli

通过 GitHub CLI `gh` 让 AstrBot 执行 GitHub 操作。

> 💡 **使用建议 / Quick Guide**
> - **轻量安全托管**：当你不想让 Bot 拥有完整的 Agent（系统执行）权限，却又希望它能安全地帮你管理 GitHub 仓库时，可以使用本插件。它提供了严格的指令沙盒与权限策略拦截。
> - **全权 Agent 场景**：若你的 Bot 已经具备了完整的 Agent 权限，则直接通过终端命令执行 `gh` 会更高效，无需额外安装此插件。

---

## 功能

- 直通 GitHub CLI 全部子命令：`/gh issue list -R owner/repo`
- LLM 工具入口：模型可在明确 GitHub 操作意图时调用 `github_cli`
- 可配置 Token、默认仓库、提交人信息、超时、输出截断、白名单和权限策略
- 默认拦截写入类和危险操作，避免误删仓库或 release

## 前置条件

主机需要安装 GitHub CLI：

```bash
gh --version
```

认证方式二选一：

```bash
gh auth login
```

或在插件配置中填写 `github.github_token`。

## 命令示例

```text
/gh auth status
/gh repo view AstrBotDevs/AstrBot
/gh issue list -R AstrBotDevs/AstrBot
/gh pr view 1 -R AstrBotDevs/AstrBot
/github workflow list -R AstrBotDevs/AstrBot
```

LLM 工具可理解的自然语言示例：

```text
查看 issue
查看 PR 1
搜索仓库 AstrBot
搜索议题 bug
查看工作流
```

## 配置说明

### github

- `default_owner`：默认仓库 owner
- `default_repo`：默认仓库名
- `append_default_repo`：对 issue/pr/release/workflow/run/label 自动追加 `-R owner/repo`
- `github_token`：可选 Token，会注入 `GH_TOKEN` 和 `GITHUB_TOKEN`
- `git_author_name`：提交人名称，会注入 `GIT_AUTHOR_NAME` 和 `GIT_COMMITTER_NAME`
- `git_author_email`：提交人邮箱，会注入 `GIT_AUTHOR_EMAIL` 和 `GIT_COMMITTER_EMAIL`

### features

- `enable_direct_command`：启用 `/gh`、`/github`、`/github-cli`
- `enable_llm_tool`：启用 LLM 工具 `github_cli`

### security

- `require_admin`：默认 true，仅 Bot 管理员可用
- `allowed_sender_ids`：用户白名单，留空不额外限制
- `allow_mutating_operations`：默认 false，关闭写入类操作
- `block_dangerous_operations`：默认 true，拦截高风险操作
- `blocked_subcommands`：额外拦截根命令或二级命令
- `allowed_root_commands`：根命令允许列表，留空允许全部根命令

### runtime

- `gh_path`：`gh` 可执行文件路径
- `command_timeout`：命令超时秒数
- `max_output_chars`：最大输出字符数
- `dry_run`：试运行，只展示命令不执行

## 安全建议

建议生产环境保持：

- `require_admin = true`
- `allow_mutating_operations = false`
- `block_dangerous_operations = true`
- 为高权限 Token 使用最小权限 scope

如果确实要创建 issue、PR、release 等写入动作，再手动开启 `allow_mutating_operations`。
