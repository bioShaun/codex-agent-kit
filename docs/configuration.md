# 配置维护

`bundle/` 是可编辑配置源。新增角色时添加对应 TOML，文件名应等于 `name`；安装器自动注册角色。删除仓库里的角色文件不会自动删除各机器旧角色，需逐机核对引用并手工移除，防止清理掉本机自定义配置。

`bundle/config.toml` 只放需要跨机器同步的公共设置，不要将整个用户级配置复制进来。主模型和账号配置由各机器自行维护。模型目录、provider URL、认证和本机项目路径均不属于此仓库。

本机资源策略由用户级及项目级 `AGENTS.md` 维护，包括调度工具（如 `slot`）、重任务的时间/内存阈值、日志要求和临时目录限制。`bundle/astra-planner.md` 与角色配置只规定遵守适用规则及在 TaskSpec 中传递规则的责任，不重复固化某台机器的策略。适用规则要求的工具缺失时，应注明规则来源并报告相关检查尚未执行。

调度协议里的 `@@CODEX_HOME@@` 表示实际安装路径，`@@CODEX_HOME_SHELL@@` 表示适合 shell 参数的引用形式；安装时替换。Python helper 与 Bash launcher 均保留现有实现。调度协议中的历史验证记录仅是配置源的历史，迁移到新机器后须重新验证运行时行为。

当前设置依据本机已安装配置提取，未承诺适配所有 Codex 版本。项目级配置可以覆盖用户级配置；同名角色也可能被覆盖。核对实际运行时元数据，不能仅凭角色自述认定模型或权限已生效。

参考 OpenAI 官方文档：

- [Subagents：角色与配置](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Configuration Reference：用户级及项目级设置](https://learn.chatgpt.com/docs/config-file/config-reference)
