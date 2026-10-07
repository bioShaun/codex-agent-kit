# 审查请求、证据与接受

## TaskSpec

新申请的普通和严格审查默认使用 `review-workflow.py` 准备、检查及接收，避免手写请求、快照哈希和验收状态。该工具复用既有 `review-contract.py` 校验；直接文件请求的旧 API 仅供已经启动的审查和兼容场景，不能把兼容路径当作绕过校验的理由。

Root 在本会话首次审查及获知协议更新后，运行 `python3 @@CODEX_HOME_SHELL@@/review-workflow.py status`，阅读变更后的相关协议，记录其 `protocol_sha256`。prepare 必须显式传入该值；旧值被拒绝后先刷新相关章节，不自动抓取新值重试来冒充已刷新。此机制检测版本不一致，不能证明模型实际阅读，也不能给旧会话主动推送指令。

准备文件 `spec.json` 包含 `review_kind`、`scope_id`、`isolation_requirement`、`instructions_file`、`files`、`required_files`。路径相对 spec 所在目录解析。instructions_file 是完整、中性的 TaskSpec 文本；files 显式列出要冻结的文件，不递归扫描整个项目；required_files 由 Root 从 Acceptance 独立列出，不能从 files/manifest 自动复制。final 另需非空 `validation_evidence`，所列文件同时显式列入 files 和 required_files；工具只核对冻结与覆盖，不判断日志是否证明通过。

```sh
python3 @@CODEX_HOME_SHELL@@/review-workflow.py prepare --spec spec.json --output review-round-1 --protocol-sha256 RECORDED_SHA256
python3 @@CODEX_HOME_SHELL@@/review-workflow.py check review-round-1
```

输出目录必须不存在，父目录必须已存在。工具流式计算文件哈希、生成 snapshot.json，再按该文件的实际字节计算 snapshot_id，生成 request.json、brief.md 和 workflow.json。派发普通 Reviewer 前 check 成功后，将完整 request.json 原样交给 fresh Reviewer。strict 使用 [执行协议](execution.md) 中 launcher 的目录入口。check 在派发前和接收时检查控制文件绑定及全部已声明文件的内容哈希，包括未变化基线；大文件/大量网络盘读取仍按当前环境的资源规则执行，不以“预检”名义绕过资源限制。

prepare 前确认实现者、测试及后台日志写入均已停止；已冻结的 spec、验证日志和源文件在本轮 receive 完成前不得继续修改。持续追加的日志应在写入结束后保存本轮独立证据文件，并将原始日志位置保留为来源。receive 的状态输出和后续运行日志不能写回本轮绑定输入。遇到 `file_stability` 诊断时保存 path/fd 前后元数据及 changed_fields；这些字段只说明观察到的变化，不能证明具体写入者或内容变更。先核对写入停止和文件系统状态，不循环重试、不关闭 inode/mtime/ctime 校验。若实际内容或范围变化，建立新快照并 fresh review；确认内容未变且原错误仅为暂态文件状态后，才对原包再做一次完整 check/receive，并保留失败证据。

可选 `baseline_manifest` 提供上轮快照，只用于生成新增/修改/删除/未变计数及变更索引；可选 `prior_findings_file` 提供上轮已完成审查的 findings 与本轮处理证据，须显式列入 files 和 required_files。brief 不展开成千上万未变化路径，不继承上次 PASS，不省略内容完整性校验，不自动缩小本轮 Acceptance。Reviewer 根据变更影响决定直接复核边界，不能将 delta=0 当作无需审查。

审查请求使用一份 JSON 作为唯一派发依据（可复用已有任务/审查记录，不另设台账）：四个必填审查字段与非空 `instructions` 在同一对象中，后者保存完整、中性的 TaskSpec。派发普通 Reviewer 前运行 `python3 @@CODEX_HOME_SHELL@@/review-contract.py preflight REQUEST.json`；strict 入口自动调用同一检查。检查失败先修请求，不消耗 Reviewer 调用。检查仅验证请求结构与声明范围，不代替 Root 判断任务是否完整。

使用文件快照时，同时提供 `snapshot_manifest`（绝对路径）和 `required_files`（本轮必需文件的绝对路径列表）。manifest 的 `files` 为绝对路径到小写 SHA256 的映射，`snapshot_id` 为 `sha256:` 加 manifest 文件自身的 SHA256。strict final 必须提供这两项；ordinary 审查不强制逐文件哈希。复用已有快照时转换为这一最小格式，保留原记录，不覆盖业务产物。Root 从 Acceptance 独立列出必需源文件、结果和实质验证证据，不能直接把 manifest 的 keys 当作 required_files，否则无法检测漏项。

preflight 校验快照标识、声明文件存在及覆盖关系；不会重新读取全部大文件计算内容哈希。它不能发现 TaskSpec 自身漏列的验收对象，也不能证明文件仍与哈希一致。Root 的冻结和 Reviewer 的独立核实仍须检查本轮要求的内容新鲜度；当前 gate 将产生的 verdict 和权限探针不得列为已存在的前置文件。

## 报告、证据和独立审查

Locator、Explorer 的结果第一行使用 complete / partial / blocked，附结论、证据位置及未覆盖范围；partial 明确证据缺口，由 Root 决定下一步。事实抽取升级到架构分析不由子代理自行派发。

实现报告包含 status、changes、files_changed、checks、risks、blockers。Worker 状态为 COMPLETED / ESCALATE / BLOCKED；COMPLETED 只表示实现者报告完成。Root 直接实现时提供同等事实摘要，作为审查包中的实现报告，不要求另建报告文件。

普通任务不为内部通信另建报告文件；项目、审查流程或 TaskSpec 明确要求的日志和证据产物照常保存。工具输出本身可作为普通检查证据；长日志存于授权路径，回传状态、关键片段和位置即可。strict 验证保留时间、证据路径及规定范围的前后哈希；ordinary 验证保留实际命令、退出码、输出、失败尝试及范围内文件变化，不默认要求逐文件哈希。

Validator 只处理已给出的精确确定性检查；Validator-complex 处理多阶段 host 流程和证据解释。后者遇到缺失或歧义步骤应 BLOCKED，不能自行补程序或宣称 host 不支持。TaskSpec 必须保留目标项目要求的完整调用形式，包括 CLI 输出模式、provider 和参数。两者区分请求的 tool call、实际 tool result 和 ledger/file 变化，保留失败尝试与修正后的重跑。只有所有要求都有当前通过证据才能 PASS；exit 0 本身不构成 PASS。缺依赖、无法运行或跳过必需检查返回 BLOCKED。禁止自动修复，披露生成物和意外变化。

Reviewer 首行严格使用 `PASS (code)` / `REQUEST_CHANGES (code)` / `BLOCKED (code)`，或对应的 `(final)`，必须复述 TaskSpec 的 review_kind。Root 使用 `^(PASS|REQUEST_CHANGES|BLOCKED) \((code|final)\)$` 完整匹配首行，并核对 kind 与原始 TaskSpec 一致。缺失或无效的必填审查字段时，Reviewer 返回裸 `BLOCKED` 并说明契约缺口，不自行猜测；裸状态、格式错误或 kind 不匹配均由 Root 记为 `contract_error`，不能升级为任何范围的 PASS。已知 kind 时其他原因导致的 BLOCKED 仍带该 kind。

新准备的审查在接收后使用统一入口，将原始 Reviewer 文本保存为文件，显式传入 Root 已核实的验证状态及证据：

```sh
python3 @@CODEX_HOME_SHELL@@/review-workflow.py receive review-round-1 --result reviewer-result.txt --validation PASS --evidence validation.log --state-output acceptance.json
```

此例不申请最终通过。仅当结果为 final PASS、必需验证和隔离证据齐全、没有未关闭必需 findings 时，Root 才追加 `--accept-final`；如果项目另要求代码审查，传入已确认的 `--code-review PASS`。code 结果只决定 code_review，不能生成 final PASS。receive 原样保存结果到独立 receipt 目录、重查绑定和内容、复用 result/state 校验，再原子发布标准字段的状态；错误返回非零并保留原始证据和 contract_error。state-output 必须位于包目录之外。能够可信识别范围时，将对应范围写成 BLOCKED，保留实际 contract_error、review_verdict 为 null，不伪造成 Reviewer 的判断；请求身份本身不可信或输出路径冲突时拒绝发布，Root 不得沿用旧台账作为当前通过依据。该工具不能验证证据语义，也不能拦截绕过它的手工写入，不宣称宿主强制门禁。

已经启动的旧审查继续按下面的兼容步骤接收，不为迁移而重跑内容审查。接收结果后，将原始 Reviewer 文本保存于已有证据目录（保留字节，不自行修空格、去 Markdown 或从正文猜测状态），运行 `python3 @@CODEX_HOME_SHELL@@/review-contract.py result REQUEST.json RESULT.txt`。严格入口也会校验其父会话最终转述首行；外层退出成功仅说明契约格式通过，不能据此自动通过验收。旧 Markdown ReviewRequest 在新严格入口中会在模型启动前被拒绝，应先转换为 JSON。

若只有首行格式错误且范围、快照、证据未变，可向原 Reviewer 要求一次仅重发格式正确的既有结论，保留原始错误和更正文本；不让 Root 自行规范化成 PASS。该更正不构成重新审查。若发生实现修改、补充实质证据、范围改变或先前独立性失效，仍按 fresh 规则处理，不以格式更正规避复审。

正文包含独立核实内容、限制，以及按严重性排序的 findings。初次审查包保持中性，不携带 Root 缺陷假设、建议 verdict 或当前 peer findings；污染后的报告不能作为独立 gate。每个 finding 必须有位置、问题、影响、证据和最小修正方向。缺少当前审查范围必需的可执行证据用 BLOCKED，不能以猜测制造修改请求或用 behavior-only review 替代证据。

### Root 的可机读状态记录

Root 在既有台账或最终结果的 JSON 代码块中分别记录代码审查、验证和最终验收；普通任务无需为此另建文件。固定字段如下，示例仅演示代码通过但验证阻塞、尚未申请最终验收，不构成通过证据：

```json
{
  "scope_id": "example-change",
  "snapshot_id": "example-snapshot",
  "code_review": "PASS",
  "validation": "BLOCKED",
  "final_acceptance": "NOT_REQUESTED",
  "review_kind": "code",
  "review_verdict": "PASS",
  "isolation_requirement": "ordinary",
  "evidence_refs": ["review-result.txt#PASS-code"],
  "contract_error": null
}
```

- `code_review`：PASS / REQUEST_CHANGES / BLOCKED / NOT_REQUESTED。
- `validation`：PASS / FAIL / BLOCKED / NOT_RUN。
- `final_acceptance`：PASS / BLOCKED / NOT_REQUESTED。
- `review_kind`：code / final；`review_verdict`：PASS / REQUEST_CHANGES / BLOCKED。审查尚未返回或契约错误时两者可为 null，不能伪造有效 verdict。
- `isolation_requirement`：ordinary / strict，记录任务要求而非声称实际权限。
- `evidence_refs`：结果所依赖的工具记录或文件位置；实际 PASS 必须有对应证据。`contract_error`：null 或具体格式/字段错误说明。

Root 将有效 `(code)` 结果写入 code_review，验证结果来自对应执行证据；不能从 `(code)` PASS 推出 final_acceptance。只有有效 `(final)` PASS、当前范围所需验证和隔离证据齐全、无必需 findings 未关闭且 contract_error 为 null，才可置 final_acceptance=PASS；若项目另要求代码审查，该项也必须通过。已申请的最终验收遇到失败或缺口为 BLOCKED；未申请保持 NOT_REQUESTED。当前代码审查契约错误记 code_review=BLOCKED；当前 final 审查契约错误记 final_acceptance=BLOCKED，保留原始结果，不从正文附注补造 verdict。范围或版本变化时使受影响状态失效并重新评估，不能沿用旧快照 PASS。结构化字段可供消费者解析，但宿主不提供自动 schema 保证；新增或已有消费者必须对契约错误拒绝通过，不把正则前缀匹配当作完整验证。

写入既有验收台账前，先用 `python3 @@CODEX_HOME_SHELL@@/review-contract.py state REQUEST.json RESULT.txt STATE.json` 校验候选状态。字段使用上述扁平枚举，不用 `accepted` 或嵌套 status 对象替代。result/state 检查失败必须记录 contract_error，并阻止对应范围的通过状态；合法 BLOCKED/REQUEST_CHANGES 的解析成功也不表示业务通过。工具输出的 schema_valid 只证明结构和状态关系，证据真实性、未关闭 findings、测试及 strict 父子探针仍由 Root 独立核对。工具无法拦截绕过它的写入，不能宣称已获得宿主级强制门禁。

普通审查的实际 turn_context 由事后审计者从 rollout 读取，取不到则记未知；不要求 Root 运行时取得子线程上下文。strict 由父入口和 Reviewer 分别产生实际权限探针证据，Root 关联入口退出状态和证据作判断。不得仅凭角色配置或要求字段宣称运行时只读隔离；当前 Codex 不应用角色 `sandbox_mode`，子代理继承父会话权限。

### 独立性与接受

Fresh 的必要条件：

- 新会话，未参与本轮实现。
- 在提供 `fork_turns` 的宿主中显式传 `fork_turns="none"`；其他宿主采用对应的无历史启动机制。默认继承不可视为隔离。
- 仅提供原始 TaskSpec、实现报告（Worker 或 Root）、Root 采集的执行前后证据、验证结果、上轮 findings；不提供 Root/Worker 的完整推理对话。
- 修复后新建 Reviewer，并携带待关闭 findings；fresh 不意味着遗忘缺陷。

Root 自行采集每轮 `A_run`、`C_report`，用两者差异判断范围和报告真实性；审查及接受时采集 `C_now`，检查结果是否漂移。原生测试可保存受控文件的执行前副本与哈希；证据格式和验证命令由目标项目 AGENTS.md 与 TaskSpec 明确。

调用成本的事后审计以每次 spawn/followup 对应的任务轮次为单位：按父线程时间线重建待处理子任务，结果尚未回传时仍属待处理。单一待处理任务时的 wait 归属该任务；多个任务时单列 shared_wait，不重复分摊；无法识别的记 unknown。报告可归属任务 wait 次数中位数/P90、样本数和零等待任务，shared/unknown 单列。汇总 wait 次数除以线程数只是比值，不能代替每任务中位数。正常任务与入口调试分组，指标采集复用日志，不强制每次调用另建报告。

优化决策以完整工作包为主要单位，单子代理轮次作为诊断明细。对需要复盘的非平凡任务，复用现有 scope/验收记录，列出 Root 与全部参与子线程的 rollout 和明确 turn_id、任务开始/最终验收时间、任务类型、实际结果、返工轮数与路由选择。`work-package-metrics.py --spec WORK.json` 只读取显式列出的轮次并输出 JSON；Root 必须确认名单覆盖相关执行、重试和审查，工具不自动证明完整。跨日统计使用事件时间；使用每条线程自身的 response_id 去重用量，缺失遥测标为不完整，不能算零成本。业务结果和返工数来自明确记录，task_complete 不等于 PASS。比较同项目同类任务的端到端耗时、Root+子代理用量与返工；未提供可靠价格不换算货币，不能只凭缓存率或子任务中位数宣布降本。小任务不强制增加指标文件，不为收集统计额外派代理。
