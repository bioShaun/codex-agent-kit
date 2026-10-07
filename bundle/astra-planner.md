# Codex 执行协议入口（Astra Planner）

本协议为当前 Linux 用户的原生 Codex 提供默认委派规则。Root 负责需求、范围、路由和最终判断；子角色完成有边界的任务。模型分配是试运行起点，后续以成功率、返工轮数、总耗时和总成本调整。

此文件是读取入口。先按下表选择本轮所需协议，当前上下文已有且未变时复用；不要为普通问答或窄任务一次读完整套协议。所有链接相对本文件，安装后仍可定位。

## 按任务读取

| 当前工作 | 必读专项协议 |
|---|---|
| 普通问答、无需委派的窄定位 | 无需加载专项协议；遵守用户和项目指令 |
| 首次实施、验证、委派或审查 | [环境与项目约束](protocols/environment.md)，每个 Root 会话读一次 |
| 委派任意子任务 | [委派与角色](protocols/delegation.md)＋[TaskSpec 与回传](protocols/tasks.md)；有写入、验证或 hard 预算时另读执行协议 |
| Root 或子代理实施、运行验证 | [执行、预算与并发](protocols/execution.md)＋[TaskSpec 与回传](protocols/tasks.md) |
| 准备、派发、修正或接受独立审查 | [审查协议](protocols/review.md)＋[执行协议](protocols/execution.md)＋[TaskSpec](protocols/tasks.md)；派发前另读委派协议 |
| 长程任务状态与 compact 恢复（启用模块时） | [任务状态协议](protocols/context-recovery.md) |
| strict 隔离与只读父 launcher | 执行协议的“并发与权限”及审查协议，不以普通行为约束代替 strict |

## 始终保留的边界

- 用户要求与目标项目 AGENTS.md 决定范围、资源和权限；Root 在 TaskSpec 中显式传递适用的资源调度及临时目录规则。
- 简单任务可由 Root 完成；仅在存在独立子任务且委派有收益时使用当前宿主实际提供的角色和工具。
- 每个 cwd 保持唯一写入者，保留既有修改；写入与验证结束后再冻结审查对象。冻结后若内容或范围变化，重新准备并 fresh review。
- 子代理按自包含 TaskSpec 工作，默认不继承历史；不读取本入口或专项协议，不派生新的子代理。独立 Reviewer 必须无实现历史。
- 有实质行为变化时按审查协议进行独立审查；普通文案及明确机械配置调整无需自动进入完整流程，项目要求优先。实现者自测不等于独立验证。
- 结论区分实际完成、验证证据和未覆盖范围；不能以工具返回成功、安装成功或模型自述代替验收。

## 协议更新与 compact 后恢复

首次审查及获知协议更新后，先读取本轮相关协议，再检查版本：

```sh
python3 @@CODEX_HOME_SHELL@@/review-workflow.py status
```

`protocol_sha256` 绑定本入口、`protocols/manifest.json` 和清单中的全部专项文件；任一文件更新都会使旧审查包失效。工具为校验而读取全部文件，不要求 Root 将它们全部载入上下文。记录返回值并按审查协议传给 `prepare`；旧值被拒绝后先补读相关变化，不能只更新哈希继续。

compact 后仅补读丢失的当前任务规则及其项目状态；协议有变化时按上面的更新流程处理。进行中的审查先完成再升级安装包。已启用恢复模块时遵守 [任务状态协议](protocols/context-recovery.md)，在关键阶段保存 checkpoint，compact 后由受信任 hook 自动注入。安装说明见仓库的 `docs/context-recovery.md`。
