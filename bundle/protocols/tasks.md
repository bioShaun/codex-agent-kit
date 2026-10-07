# TaskSpec 与常规回传

按任务需要提供字段，不为窄任务填写无关模板：

- 普通只读任务：Goal、只读 Scope、Context（路径、已知事实、未决问题和项目约束）、Acceptance、Return（状态、证据位置、限制）。默认使用 advisory 预算，由 Root 记录开始和下次评估时间即可。
- 修改任务：在上述基础上增加具体文件 ownership、Out of scope、Verification（必要命令、预期结果及真实使用场景）和预算安排。Worker-fast 必须另有明确转换规则、排除项、命中范围和验收方法。
- 验证任务：给出完整命令或操作流程、环境和前提、通过条件、允许产生的文件及独占执行窗口；明确 ordinary 或 strict 证据要求。测试不得因名称看似本地就被假定无生产访问或外部副作用。
- 审查任务：必填 `review_kind`（`code` 或 `final`）、`scope_id`、`snapshot_id` 和 `isolation_requirement`（`ordinary` 或 `strict`），并明确当前范围所需证据。kind 描述审查范围，不是预设 verdict；不向 Reviewer 提供 Root 的状态判断来代替中性事实。代码审查仅判断指定代码/文档范围；final 审查对照本轮全部 Acceptance。strict 是隔离要求，与 kind 独立，不得用 `code` 标签免除任务指定的 strict 证据。
- 严格验收任务：额外明确冻结证据范围、前后哈希或快照、完整日志位置、独立审查及运行时权限要求。Budget 写入模式、无进展判定和纠正轮数；advisory 使用 UTC `startedAt` 与 `nextAssessmentAt`，hard 使用 UTC `startedAt`、`deadline` 与取消能力。任何层级使用 hard 截止时都必须提供上述 hard 预算字段。

实现路由：Root 处理简单任务、范围决策及依赖主上下文的复杂实现；明确机械转换用 Worker-fast；边界清晰且委派有收益的行为实现用 Worker。Worker 可自主选择边界内的命名、局部算法和既有模式；Worker-fast 不承担行为设计、跨项目语义重命名、公共接口变更或复杂实现。

原生 Codex 的任务说明与报告是文本协议，不提供额外的 schema 校验保证。结构化外观不等于结构化传输。

## 常规结果与验证证据

Locator、Explorer 的结果第一行使用 complete / partial / blocked，附结论、证据位置及未覆盖范围；partial 明确证据缺口，由 Root 决定下一步。事实抽取升级到架构分析不由子代理自行派发。

实现报告包含 status、changes、files_changed、checks、risks、blockers。Worker 状态为 COMPLETED / ESCALATE / BLOCKED；COMPLETED 只表示实现者报告完成。Root 直接实现时提供同等事实摘要，作为审查包中的实现报告，不要求另建报告文件。

普通任务不为内部通信另建报告文件；项目、审查流程或 TaskSpec 明确要求的日志和证据产物照常保存。工具输出本身可作为普通检查证据；长日志存于授权路径，回传状态、关键片段和位置即可。strict 验证保留时间、证据路径及规定范围的前后哈希；ordinary 验证保留实际命令、退出码、输出、失败尝试及范围内文件变化，不默认要求逐文件哈希。

Validator 只处理已给出的精确确定性检查；Validator-complex 处理多阶段 host 流程和证据解释。后者遇到缺失或歧义步骤应 BLOCKED，不能自行补程序或宣称 host 不支持。TaskSpec 必须保留目标项目要求的完整调用形式，包括 CLI 输出模式、provider 和参数。两者区分请求的 tool call、实际 tool result 和 ledger/file 变化，保留失败尝试与修正后的重跑。只有所有要求都有当前通过证据才能 PASS；exit 0 本身不构成 PASS。缺依赖、无法运行或跳过必需检查返回 BLOCKED。禁止自动修复，披露生成物和意外变化。
