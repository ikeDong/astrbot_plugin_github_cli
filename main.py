from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from typing import Any

from astrbot.api import AstrBotConfig, FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


DANGEROUS_OPERATIONS = {
    ("auth", "logout"),
    ("repo", "delete"),
    ("repo", "archive"),
    ("repo", "rename"),
    ("repo", "transfer"),
    ("issue", "delete"),
    ("pr", "close"),
    ("release", "delete"),
    ("workflow", "disable"),
    ("workflow", "enable"),
    ("run", "delete"),
    ("cache", "delete"),
}

MUTATING_ROOTS = {
    "auth",
    "repo",
    "issue",
    "pr",
    "release",
    "workflow",
    "run",
    "cache",
    "label",
    "project",
    "secret",
    "variable",
    "gist",
    "codespace",
}

READ_ONLY_SUBCOMMANDS = {
    "help",
    "status",
    "list",
    "view",
    "diff",
    "checks",
    "clone",
    "browse",
}

GH_ROOT_COMMANDS = {
    "alias",
    "api",
    "auth",
    "browse",
    "cache",
    "codespace",
    "completion",
    "config",
    "extension",
    "gist",
    "gpg-key",
    "issue",
    "label",
    "org",
    "pr",
    "project",
    "release",
    "repo",
    "ruleset",
    "run",
    "search",
    "secret",
    "ssh-key",
    "status",
    "variable",
    "workflow",
}


@dataclass(frozen=True)
class GitHubCliResult:
    code: int
    output: str
    command: str
    blocked: bool = False


@dataclass(frozen=True)
class GitHubCliSettings:
    gh_path: str
    default_owner: str
    default_repo: str
    github_token: str
    git_author_name: str
    git_author_email: str
    command_timeout: int
    max_output_chars: int
    enable_direct_command: bool
    enable_llm_tool: bool
    require_admin: bool
    allowed_sender_ids: list[str]
    blocked_subcommands: list[str]
    allowed_root_commands: list[str]
    block_dangerous_operations: bool
    allow_mutating_operations: bool
    append_default_repo: bool
    dry_run: bool


class GitHubCliCommandBuilder:
    def __init__(self, default_repo: str = "") -> None:
        self.default_repo = default_repo

    def from_text(self, text: str) -> list[str] | None:
        stripped = text.strip()
        if not stripped:
            return None
        direct = self._parse_direct(stripped)
        if direct:
            return direct
        return self._parse_natural(stripped)

    def _parse_direct(self, text: str) -> list[str] | None:
        prefixes = ("gh ", "/gh ", "github ", "/github ", "github-cli ", "/github-cli ")
        lowered = text.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                command_text = text[len(prefix) :].strip()
                return shlex.split(command_text) if command_text else ["help"]
        if lowered in {"gh", "/gh", "github", "/github", "github-cli", "/github-cli"}:
            return ["help"]

        try:
            args = shlex.split(text)
        except ValueError:
            return None
        if args and args[0].lower() in GH_ROOT_COMMANDS:
            return args
        return None

    def _parse_natural(self, text: str) -> list[str] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        repo = self._extract_repo(normalized)
        repo_args = ["-R", repo] if repo else []

        issue_number = self._extract_number(normalized, ["issue", "议题"])
        if issue_number:
            return ["issue", "view", issue_number, *repo_args]

        pr_number = self._extract_number(normalized, ["pr", "pull request", "拉取请求"])
        if pr_number:
            return ["pr", "view", pr_number, *repo_args]

        issue_create_markers = ["创建议题", "新建议题", "创建 issue", "新建 issue"]
        if self._has_any(normalized, issue_create_markers):
            title = self._extract_after(normalized, issue_create_markers)
            return ["issue", "create", *repo_args, "--title", title or "New issue", "--body", ""]

        repo_search_markers = ["搜索仓库", "查找仓库", "搜仓库"]
        if self._has_any(normalized, repo_search_markers):
            query = self._extract_after(normalized, repo_search_markers)
            return ["search", "repos", query] if query else ["search", "repos"]

        issue_search_markers = ["搜索议题", "搜索 issue", "查找议题"]
        if self._has_any(normalized, issue_search_markers):
            query = self._extract_after(normalized, issue_search_markers)
            return ["search", "issues", query] if query else ["issue", "list", *repo_args]

        pr_search_markers = ["搜索pr", "搜索 pr", "搜索拉取请求"]
        if self._has_any(normalized, pr_search_markers):
            query = self._extract_after(normalized, pr_search_markers)
            return ["search", "prs", query] if query else ["pr", "list", *repo_args]

        clone_markers = ["克隆仓库", "clone 仓库", "克隆", "clone"]
        if self._has_any(normalized, clone_markers):
            target = self._extract_after(normalized, clone_markers)
            return ["repo", "clone", target] if target else None

        exact_patterns: list[tuple[re.Pattern[str], list[str]]] = [
            (re.compile(r"^(gh|github)(认证|登录)?状态$", re.I), ["auth", "status"]),
            (re.compile(r"^(查看|列出)?(我的)?(所有)?(仓库|repos?|repository|repositories)$", re.I), ["repo", "list"]),
            (re.compile(r"^(查看|列出)?(仓库|repo)信息$", re.I), ["repo", "view", *repo_args]),
            (re.compile(r"^(查看|列出)?(issue|议题)$", re.I), ["issue", "list", *repo_args]),
            (re.compile(r"^(查看|列出)?(pr|pull request|拉取请求)$", re.I), ["pr", "list", *repo_args]),
            (re.compile(r"^(查看|列出)?(release|发布|版本)$", re.I), ["release", "list", *repo_args]),
            (re.compile(r"^(查看|列出)?(workflow|工作流)$", re.I), ["workflow", "list", *repo_args]),
            (re.compile(r"^(查看|列出)?(run|运行记录|工作流运行)$", re.I), ["run", "list", *repo_args]),
            (re.compile(r"^(查看|列出)?(label|标签)$", re.I), ["label", "list", *repo_args]),
        ]
        for pattern, command in exact_patterns:
            if pattern.search(normalized):
                return command

        if repo:
            if self._has_any(normalized, ["issue", "议题"]):
                return ["issue", "list", *repo_args]
            if self._has_any(normalized, ["pr", "pull request", "拉取请求"]):
                return ["pr", "list", *repo_args]
            if self._has_any(normalized, ["release", "发布", "版本"]):
                return ["release", "list", *repo_args]
            if self._has_any(normalized, ["workflow", "工作流"]):
                return ["workflow", "list", *repo_args]
            if self._has_any(normalized, ["label", "标签"]):
                return ["label", "list", *repo_args]

        if self._has_any(normalized.lower(), ["github", "gh "]):
            return ["help"]
        return None

    def _extract_repo(self, text: str) -> str:
        match = re.search(r"([\w.-]+/[\w.-]+)", text)
        return match.group(1) if match else self.default_repo

    def _extract_number(self, text: str, words: list[str]) -> str:
        for word in words:
            match = re.search(rf"{re.escape(word)}\s*#?(\d+)", text, re.I)
            if match:
                return match.group(1)
        return ""

    def _extract_after(self, text: str, markers: list[str]) -> str:
        lowered = text.lower()
        for marker in markers:
            index = lowered.find(marker.lower())
            if index >= 0:
                return text[index + len(marker) :].strip(" ：:，,。")
        return ""

    def _has_any(self, text: str, markers: list[str]) -> bool:
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in markers)


class GitHubCliExecutor:
    def __init__(self, settings: GitHubCliSettings) -> None:
        self.settings = settings

    async def run(self, args: list[str]) -> GitHubCliResult:
        normalized_args = self._normalize_args(args)
        blocked_reason = self._blocked_reason(normalized_args)
        command_display = self._display_command(normalized_args)
        if blocked_reason:
            return GitHubCliResult(2, blocked_reason, command_display, blocked=True)
        if self.settings.dry_run:
            return GitHubCliResult(0, f"dry-run: {command_display}", command_display)

        gh_binary = self._resolve_gh()
        process_env = os.environ.copy()
        if self.settings.github_token:
            process_env["GH_TOKEN"] = self.settings.github_token
            process_env["GITHUB_TOKEN"] = self.settings.github_token
        if self.settings.git_author_name:
            process_env["GIT_AUTHOR_NAME"] = self.settings.git_author_name
            process_env["GIT_COMMITTER_NAME"] = self.settings.git_author_name
        if self.settings.git_author_email:
            process_env["GIT_AUTHOR_EMAIL"] = self.settings.git_author_email
            process_env["GIT_COMMITTER_EMAIL"] = self.settings.git_author_email

        process = await asyncio.create_subprocess_exec(
            gh_binary,
            *normalized_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.settings.command_timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return GitHubCliResult(124, "gh 命令执行超时。", command_display)

        output = "\n".join(
            part.decode("utf-8", errors="replace").strip()
            for part in (stdout, stderr)
            if part and part.strip()
        ).strip()
        if not output:
            output = "命令执行完成，无输出。"
        if len(output) > self.settings.max_output_chars:
            output = f"{output[: self.settings.max_output_chars]}\n...输出已截断"
        return GitHubCliResult(process.returncode or 0, output, command_display)

    def _normalize_args(self, args: list[str]) -> list[str]:
        clean_args = [arg for arg in args if arg]
        if self.settings.append_default_repo and self.settings.default_owner and self.settings.default_repo:
            return self._inject_default_repo(clean_args)
        return clean_args

    def _inject_default_repo(self, args: list[str]) -> list[str]:
        if not args or "-R" in args or "--repo" in args:
            return args
        root = args[0]
        if root not in {"issue", "pr", "release", "workflow", "run", "label"}:
            return args
        if len(args) < 2:
            return args
        repo = f"{self.settings.default_owner}/{self.settings.default_repo}"
        return [root, args[1], "-R", repo, *args[2:]]

    def _blocked_reason(self, args: list[str]) -> str:
        if not args:
            return "空命令已拦截。"
        root = args[0]
        if self.settings.allowed_root_commands and root not in self.settings.allowed_root_commands:
            return f"gh {root} 不在允许的根命令列表中。"
        joined = " ".join(args[:2])
        if root in self.settings.blocked_subcommands or joined in self.settings.blocked_subcommands:
            return f"gh {joined} 已被配置拦截。"
        if self.settings.block_dangerous_operations and len(args) >= 2:
            if (args[0], args[1]) in DANGEROUS_OPERATIONS:
                return f"危险操作 gh {joined} 已被拦截。"
        if not self.settings.allow_mutating_operations and self._looks_mutating(args):
            return f"写入类操作 gh {joined} 已被拦截。"
        return ""

    def _looks_mutating(self, args: list[str]) -> bool:
        if not args or args[0] not in MUTATING_ROOTS:
            return False
        if len(args) == 1:
            return False
        return args[1] not in READ_ONLY_SUBCOMMANDS

    def _resolve_gh(self) -> str:
        gh_path = self.settings.gh_path.strip() or "gh"
        if os.path.isabs(gh_path) and os.access(gh_path, os.X_OK):
            return gh_path
        resolved = shutil.which(gh_path)
        if resolved:
            return resolved
        raise FileNotFoundError("未找到 gh，可在配置里设置 gh_path。")

    def _display_command(self, args: list[str]) -> str:
        return "gh " + " ".join(shlex.quote(arg) for arg in args)


class GitHubCliPlugin(Star):
    """通过 GitHub CLI 执行 GitHub 操作。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.settings = self._load_settings()
        self.builder = GitHubCliCommandBuilder(self._default_repo_slug())
        self.executor = GitHubCliExecutor(self.settings)
        if self.settings.enable_llm_tool:
            self.context.add_llm_tools(self._build_llm_tool())

    @filter.command("gh", alias={"github", "github-cli"})
    async def gh_command(self, event: AstrMessageEvent) -> Any:
        """执行 GitHub CLI 命令。"""
        if not self.settings.enable_direct_command:
            yield event.plain_result("GitHub CLI 命令入口已关闭。")
            return
        text = event.get_message_str()
        args = self.builder.from_text(text)
        if not args:
            yield event.plain_result(self._usage())
            return
        result = await self._execute_for_event(event, args)
        yield event.plain_result(self._format_result(result))

    @filter.command("ghhelp", alias={"github帮助", "github-cli-help"})
    async def gh_help(self, event: AstrMessageEvent) -> Any:
        """查看 GitHub CLI 插件帮助。"""
        yield event.plain_result(self._usage())

    def _build_llm_tool(self) -> FunctionTool:
        async def github_cli_handler(
            *handler_args: Any,
            command: str | None = None,
            natural_language: str | None = None,
        ) -> str:
            event = next(
                (
                    arg
                    for arg in handler_args
                    if hasattr(arg, "get_sender_id") and hasattr(arg, "is_admin")
                ),
                None,
            )
            text = str(command or natural_language or "").strip()
            if not text:
                return "缺少 command 或 natural_language。"
            args = self.builder.from_text(text) or shlex.split(text)
            result = await self._execute_for_event(event, args)
            return self._format_result(result)

        return FunctionTool(
            name="github_cli",
            description=(
                "通过受控 GitHub CLI 执行 GitHub 操作。支持全部 gh 子命令，但会按插件配置进行权限和危险操作拦截。"
                "当用户明确要求查看仓库、issue、PR、workflow、release、搜索或执行 gh 命令时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "GitHub CLI 命令，可以带 gh 前缀，也可以只写子命令，例如 issue list -R owner/repo。",
                    },
                    "natural_language": {
                        "type": "string",
                        "description": "可选，自然语言描述；command 为空时尝试解析它。",
                    },
                },
                "required": [],
            },
            handler=github_cli_handler,
        )

    async def _execute_for_event(
        self, event: AstrMessageEvent, args: list[str]
    ) -> GitHubCliResult:
        allowed, reason = self._check_permission(event)
        if not allowed:
            return GitHubCliResult(3, reason, "gh " + " ".join(args), blocked=True)
        try:
            return await self.executor.run(args)
        except Exception as exc:
            logger.exception("GitHub CLI command failed")
            return GitHubCliResult(1, f"执行失败：{exc}", "gh " + " ".join(args))

    def _check_permission(self, event: AstrMessageEvent) -> tuple[bool, str]:
        sender_id = str(event.get_sender_id())
        if self.settings.allowed_sender_ids and sender_id not in self.settings.allowed_sender_ids:
            return False, "你不在 GitHub CLI 插件白名单里。"
        if self.settings.require_admin and not event.is_admin():
            return False, "需要 Bot 管理员权限。"
        return True, ""

    def _load_settings(self) -> GitHubCliSettings:
        return GitHubCliSettings(
            gh_path=self._get_str("runtime", "gh_path", "gh"),
            default_owner=self._get_str("github", "default_owner", ""),
            default_repo=self._get_str("github", "default_repo", ""),
            github_token=self._get_str("github", "github_token", ""),
            git_author_name=self._get_str("github", "git_author_name", ""),
            git_author_email=self._get_str("github", "git_author_email", ""),
            command_timeout=self._get_int("runtime", "command_timeout", 60),
            max_output_chars=self._get_int("runtime", "max_output_chars", 6000),
            enable_direct_command=self._get_bool("features", "enable_direct_command", True),
            enable_llm_tool=self._get_bool("features", "enable_llm_tool", True),
            require_admin=self._get_bool("security", "require_admin", True),
            allowed_sender_ids=self._get_list("security", "allowed_sender_ids", []),
            blocked_subcommands=self._get_list("security", "blocked_subcommands", []),
            allowed_root_commands=self._get_list("security", "allowed_root_commands", []),
            block_dangerous_operations=self._get_bool("security", "block_dangerous_operations", True),
            allow_mutating_operations=self._get_bool("security", "allow_mutating_operations", False),
            append_default_repo=self._get_bool("github", "append_default_repo", True),
            dry_run=self._get_bool("runtime", "dry_run", False),
        )

    def _group(self, name: str) -> dict[str, Any]:
        value = self.config.get(name, {}) if self.config else {}
        return value if isinstance(value, dict) else {}

    def _get_str(self, group: str, key: str, default: str) -> str:
        return str(self._group(group).get(key, default)).strip()

    def _get_int(self, group: str, key: str, default: int) -> int:
        try:
            return int(self._group(group).get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_bool(self, group: str, key: str, default: bool) -> bool:
        value = self._group(group).get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "开", "开启"}
        return bool(value)

    def _get_list(self, group: str, key: str, default: list[str]) -> list[str]:
        value = self._group(group).get(key, default)
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return default

    def _default_repo_slug(self) -> str:
        if self.settings.default_owner and self.settings.default_repo:
            return f"{self.settings.default_owner}/{self.settings.default_repo}"
        return ""

    def _format_result(self, result: GitHubCliResult) -> str:
        data = {
            "command": result.command,
            "exit_code": result.code,
            "blocked": result.blocked,
            "output": result.output,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _usage(self) -> str:
        return "\n".join(
            [
                "GitHub CLI 插件用法：",
                "/gh auth status",
                "/gh issue list -R owner/repo",
                "/github pr view 1 -R owner/repo",
                "自然语言：查看 issue、查看 PR 1、搜索仓库 AstrBot、查看工作流。",
                "配置项可控制 Token、默认仓库、管理员限制、白名单、危险操作拦截。",
            ]
        )
