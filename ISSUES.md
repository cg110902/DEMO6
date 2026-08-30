# Novel Studio 工程项目问题清单

> 结论先行：**该项目目前无法开箱运行**——两处语法/逻辑 bug 直接阻断 `studio.py` 导入和 `check` 命令。
> 本清单在"临时修复这两个阻断项以便测试"的前提下，通过实际运行 `init → check → pack → sync → snapshot → export` 全流程、构造异常输入、跨卷/跨书场景、读取全部源码与协议文档后整理得出。
> 已确认问题分五档：🔴 阻断级 / 🟠 数据一致性级 / 🟡 逻辑不一致级 / 🟢 契约与文档级 / ⚪ 健壮性·安全·体验级。

## 修复状态总览

| # | 严重度 | 问题 | 状态 | 修复位置 |
|---|---|---|---|---|
| 1 | 🔴 | pack.py export_txt 缩进错误 | ✅ 已修复 | engine/pack.py |
| 2 | 🔴 | checks.py 元组误格式化崩溃 | ✅ 已修复 | engine/checks.py |
| 3 | 🟠 | sync 先合并归档后体检 | ✅ 已修复 | engine/state.py（落盘前 verify_data）+ cli.py |
| 4 | 🟠 | 空提案被当合并并封存 | ✅ 已修复 | engine/state.py（no-op 识别）+ cli.py（beats 闸门） |
| 5 | 🟠 | init --clean 稿/状态脱节 | ✅ 已加固 | engine/cli.py（明确提示语义） |
| 6 | 🟡 | 跨卷同章号 raw/beats 漏报 | ✅ 已修复 | engine/checks.py（(vol,n) 键控） |
| 7 | 🟡 | 章节号口径混用 | ✅ 已澄清 | agents/rules/novel_workflow.md |
| 8 | 🟢 | 完成依赖 warning、空提案可推进 | ✅ 已修复 | engine/cli.py（sync 需 beats）+ state.py（no-op） |
| 9 | 🟢 | review_gate 24 字符硬阈值 | ✅ 已修复 | engine/checks.py（引文/字段判据） |
| 10 | 🟢 | proposal 骨架 current 仅 3 字段 | ✅ 已修复 | engine/cli.py（9 字段） |
| 11 | 🟢 | pack 超预算只报不裁 | ✅ 已修复 | engine/pack.py（P2 硬裁） |
| 12 | 🟢 | 缺 .gitignore | ✅ 已修复 | .gitignore 新增 |
| 13 | 🟢 | export 文件名未净化 | ✅ 已修复 | engine/pack.py（_safe_filename） |
| 14 | 🟢 | proposal --write 输出顺序 | ✅ 已修复 | engine/cli.py（flush） |
| 15 | 🟢 | status --json 多书 exists:false | ✅ 已修复 | engine/cli.py（reason 字段） |
| 16 | 🟢 | init 相对路径建到仓库根 | ✅ 已修复 | engine/cli.py（_init_workspace） |
| 17 | 🟢 | status 无定稿显示 ch_000 | ✅ 已修复 | engine/cli.py |
| 18 | ⚪ | pack 静默降级无说明 | ✅ 已注明 | engine/pack.py（docstring） |

**回归验证**：新增 `engine/tests/test_smoke.py`（纯 stdlib unittest），8 例覆盖 #1/#2/#3/#4/#9/#16/#17 及全流程，`python -m unittest engine.tests.test_smoke` 全部通过。

---

## 🔴 阻断级（不修则引擎无法使用）

### 1. `engine/pack.py` `export_txt` 第 279 行缩进错误 → 整个 CLI 无法启动　✅ 已修复
- **现象**：任何命令（`--version`、`help`、`status`）都抛出 `IndentationError: unexpected indent`，因为 `studio.py` 导入链 `engine.cli` → `engine.pack` 就在这一步失败。引擎完全不可用。
- **位置**：`engine/pack.py`，`export_txt()` 内：
  ```python
  for vol in vols:
      body = [ ... for ... if v == vol]
              if body:          # ← 缩进错位（与上一行的列表推导式对齐）
          parts.append(...)
  ```
  `if body:` 应缩进到 `body = ...` 同级。
- **已验证**：`python studio.py --version` 直接报 `IndentationError`。
- **状态**：已临时修复（`if body:` 减一级缩进），引擎得以启动。

### 2. `engine/checks.py` `run_checks` 第 213 行把 `(卷, 章号)` 元组当章号 → `check` 崩　✅ 已修复
- **现象**：只要书里存在任一 `final` 定稿，`python studio.py check` 就崩溃：
  `TypeError: unsupported format string passed to tuple.__format__`，位置在 `tok = f"ch_{n:03d}"`。
- **原因**：`per_ch` 字典的键是 `(vol, n)` 元组（为了支持跨卷同章号），但流程事实循环写作
  `for n in sorted(per_ch): tok = f"ch_{n:03d}"`，把键当成整型章号。同时 `if n not in raw_nums` 用元组去 `in` 整型集合，永远为真，导致误报。
- **已验证**：对含 1 章定稿的书跑 `check` 复现崩溃。
- **状态**：已临时修复（改为 `for (vol, n) in sorted(per_ch)` 并解包出 `n`）。
- **连带**：修复前 `check` 一挂，`sync` 的"体检"链路、`export` 前的验收都无法进行，属于核心命令级故障。

---

## 🟠 数据一致性级（会造成状态污染，最需警惕）

### 3. `sync` 顺序缺陷：先"合并并归档提案"，后"体检"，体检失败会留下脏状态　✅ 已修复
- **现象**：`cmd_sync` 先调用 `apply_inbox`（这一步就把提案合并进六个状态文件，并把提案归档到 `inbox/processed/`），**之后**才调 `state.verify_state` 做体检。
- **后果**：若体检失败（典型：提案 `current.present_characters` 引用了未登记的实体名），
  - 状态已被真实修改（`present_characters` 里出现未登记人物）；
  - 提案已被归档到 `processed/` 而非 `failed/`，**无法走"去 failed/ 捡回再改再 sync"的恢复路径**；
  - 未生成快照，回滚只能靠手动；
  - 命令返回码 1，但数据已不可逆地写入。
- **实测复现**：构造一个提案，`current.present_characters = ["沈拓","张三"]`，`entities` 只 upsert 了"沈拓"（张三未登记）。`sync` 输出 `合并 1`、`归档 → processed/ch_001.2.json`，然后报
  `current.present_characters 引用未登记实体「张三」`，快照未生成。事后 `current.json` 的 `present_characters` 确实含"张三"，提案在 `processed/`，`failed/` 为空。
- **与文档矛盾**：`novel_workflow.md` 声称"sync 失败 → 提案自动进 failed/：读报错改文件，再 sync（引擎自动捡回）"。但**只有"合并失败"才进 failed/，体检失败则已进 processed/**。契约只覆盖一半。
- **建议**：体检应在**内存合并结果上先跑**、全部通过再落盘；或落盘后体检失败触发状态回滚 + 提案回置 failed/。

### 4. 空提案（六区全空）会被当作"合并 1"并创建快照、推进流水线　✅ 已修复
- **现象**：一份只含 `schema`/`chapter`/`operation_id`、六区全空的合法提案，`sync --dry-run` 显示"合并 1"；正式 `sync` 创建快照 `ch_XXX_done`。
- **原因**：`cmd_sync` 的 `no_op` 判断只在 `applied_now == 0 and duplicates == 0` 时拒绝；而空提案被 `apply_inbox` 计为 `applied=1`（无重复、无错误），于是绕过了"空转拒绝"闸门。
- **实测复现**：`ch_002` 只有一行 final、提案六区全空，`sync` 成功并生成 `ch_002_done` 快照；`status` 随即显示该章 final ✅ / merged ✅ / snapshot ✅，但 **beats ·、raw ·**（五格并未全绿）。
- **影响**：允许在无细纲、无草稿、零状态更新的情况下"封存一章"，与"五格全绿"完成契约冲突，且污染 `latest_finalized` 推进逻辑。

### 5. `init --clean` 清空稿件但保留状态/快照 → 稿与状态脱节　✅ 已修复
- **现象**：对已登记书执行 `init --clean`，删除 `manuscript/` 但保留 `state/`（六 JSON + snapshots）与 `outlines/`。
- **后果**：清稿后 `status` 仍显示该章 `merged ✅ / snapshot ✅`（因为 `.applied_operations.json` 与快照还在），但 `final` 已删除、`manuscript` 不存在。状态机说"已完成"，实际无正文；`sync` 该章会因"无 final"被拒。重新写稿需全新 `operation_id` 提案。
- **属于有意为之但从设计角度是遗留的"半清"状态**，建议在帮助文案或文档中对"清稿后状态仍保留"给出明确预期，或提供清稿时同步清状态的选项。

---

## 🟡 逻辑不一致级

### 6. 跨卷同章号：`final_without_raw/beats` 全局章号集合导致漏报　✅ 已修复
- **现象**：代码在 `ver_by_ch`/`per_ch`/`export_txt`/`evidence.final_chapters` 各处用 `(vol, n)` 作键，注释明确"跨卷同章号互不覆盖（vol_02/ch_001 合法）"。但 `check` 里 `raw_nums`、`beats_nums` 是**全局整型章号集合**。
- **后果**：当 `vol_02/ch_001` 有 final 但**没有** raw 和 beats 时，`check` 不会报 `final_without_raw/beats`——因为全局章号 `1` 已被 `vol_01/ch_001` 的 raw/beats "覆盖"。跨卷真实缺口被静默吞掉。
- **实测复现**：在 `第二本` 创建 `vol_02/ch_001` final（无 raw/beats），`check` 的 `final_without_*` 只报了 `ch_002`，未报 `ch_1`。

### 7. 章节编号"全局 vs 卷内"语义在代码中混用　✅ 已修复
- **文档**（`novel_craft.md`）："台账章号只有全局口径……ch_012 就是第十二个定稿章，与卷号无关；卷内编号只是显示糖"。
- **代码**：一方面 `per_ch`/`export`/`final_chapters` 用 `(vol, n)` 键支持跨卷同号；另一方面 `issues`、`raw_nums`、`beats_nums`、`status` 的 pipeline、`evidence.gaps` 的 `latest_chapter_number` 都按全局章号处理。两套口径并存，边界模糊。
- **影响**：跨卷场景下 `status` 流水线行（按标量章号枚举）、`evidence.mentions` 的"章号"、`word_band_deviation` 的 `tok`（不含卷号）都会把不同卷的同号章混为一谈。建议统一口径并明确"卷内是否会重排章号"。

---

## 🟢 契约与文档级

### 8. 完成"五格全绿"依赖 warning，不阻断　✅ 已修复
- `final_without_raw` / `final_without_beats` 只是 `warnings`（不返回码 1）。空提案（#4）正是利用这条把无细纲、无草稿的章推进到"已定稿"。若要使"发表标准"硬性，可考虑把"最终章缺 raw/beats"设为 error 或在 `sync` 前加闸门。

### 9. `review_gate` 对审校注记证据线强制 `len(line) >= 24` 字符　✅ 已修复
- **现象**：`checks.review_gate` 对每条"验收"判定要求整行 ≥ 24 字符（`len(line) < 24` 判未审）。实测第一次简洁但有效的证据行（如"✓ 李默与老板对话（见正文引用段）"）被拒："打了判定符但证据线过短"。
- **问题**：24 字符是任意硬阈值，会误伤"语句精炼但已给证据"的注记；且按行长度而非"是否有引文/数据"做判定，判别逻辑粗糙。建议改为"必须含至少一段正文引文或 evidence 字段名+数值"的结构化判据，或放宽/可配置。

### 10. `proposal new` 骨架 `current` 只含 3 字段，与 schema 的 9 字段不对应　✅ 已修复
- 骨架的 `current` 仅打印 `time/location/present_characters`，而 `current.schema.json` 定义了 9 个可写字段（含 `power_level/abilities/injury/equipment/assets/situation`）。虽不违反 schema（其余字段可省），但"骨架"会引导用户漏写这些字段。建议骨架完整预填 9 键（空值）。

### 11. pack"超预算硬裁"只报不裁　✅ 已修复
- `engine/README.md`：pack "超预算按优先级硬裁"。但 `build_pack` 仅计算 `budget_report.over_budget` 标志（`PACK_TOKEN_CAP = 12000`），**没有任何裁剪动作**；`file_index` 仅在渲染层 `[:25]` 截断展示，P2 冷索引并未按预算裁剪。文档描述与实现不符（属"虚张声势"式的流水线提示）。

### 12. 仓库缺少 `.gitignore`，但文档/代码多处引用它　✅ 已修复
- `engine/common.py` 注释与 `README.md` 都写明 `workspace/` 应被忽略（"见仓库 .gitignore"），`pyproject` 也未忽略。但仓库**根本没有 `.gitignore` 文件**。
- 实测：`git status` 显示 `workspace/` 与 `engine/__pycache__/` 均未忽略，会被提交进版本库——而这正是设计上要排除的书稿正文与缓存。
- **修复**：新增 `.gitignore`，至少忽略 `workspace/`、`__pycache__/`、`*.pyc`、`export/` 等运行/数据产物。

---

## ⚪ 健壮性 / 安全 / 体验级

### 13. `export_txt` 用 `project.json.title` 直接拼文件名，未过 `safe_child_path`　✅ 已修复
- `out / f"{title}.txt"`：若 `title` 含 `/` 或 `..` 会产生嵌套目录或（相对 export 根）越界路径。`common.safe_child_path` 只用于 `pack --open`，未用于导出文件名。低危（title 由用户自控），建议对导出文件名做清洗/白名单。

### 14. `cmd_proposal --write` 的 stdout/stderr 输出顺序错乱　✅ 已修复
- 写盘后：`print(骨架已写入, stdout)` 与 `print(填六区…, stderr)` 顺序颠倒显示（stdout 块缓冲），终端里先看到 stderr 提示。轻微，可统一走 stderr 或 flush。

### 15. `status --json` 多书时 `exists: false` 语义误导　✅ 已修复
- 存在多本书且未指定 `-w` 时，`status --json` 返回 `{"exists": false, "books": [...]}`。书是存在的，只是"有歧义未定"，用 `exists:false` 表达"未解析到唯一书"含义不清，建议改字段或改值为 `"ambiguous"`。

### 16. `init -w 我的书`（漏写 `workspace/` 前缀）会在仓库根建书　✅ 已修复
- `common.resolve_workspace` 对相对路径直接 `project_root()/p`，不会自动补 `workspace/`。用户写成 `init -w 我的书` 会在仓库根目录（而非 `workspace/` 下）创建书，造成目录混乱。可考虑对不带 `workspace/` 前缀的相对路径给出提示或自动归位。

### 17. `status` 无定稿时显示"最新 ch_000"　✅ 已修复
- `_book_brief` 中 `latest = 0` 时输出 `最新 ch_000`（`f"ch_{latest:03d}"`）。0 章时显示 `ch_000` 作为"最新章"占位易误导，建议显示"无"或 `(未定稿)`。

### 18. 状态"绝不静默兜底"哲学在 pack 层被局部放宽　✅ 已修复
- `common.py` 明确"状态文件损坏绝不静默兜底为空默认值——必须让调用方显式失败"。但 `pack._hard_reminders` 用 `try: state.load_state("lines") except ValueError: lines = {foreshadows:[], misunderstandings:[]}` 静默降级为空。这有利于 pack 不要因台账损坏而崩，但与上层"绝不兜底"哲学不一致，宜在注释或文档中说明这一层是**容错例外**。

---

## 总体评估

- **架构立意清晰**：`协议文档(AGENTS/rules/skills) + 确定性引擎(engine，纯 stdlib)`，LLM 管判断、引擎管白名单死板事，五阶段流水线（0 初始化 → 1 细纲 → 2 起草 → 3 重铸 → 4 同步）角色/权限矩阵、幂等提案制、账本重算、快照回滚等设计都相当成熟。
- **但当前状态不可交付**：两个阻断 bug（#1/#2）让引擎连 `--version` 和 `check` 都跑不起来；`sync` 的"合并→体检"顺序缺陷（#3）和空提案漏洞（#4）会在正常使用中造成**状态污染与不可恢复的历史缺失**，属高风险数据完整性问题；`workspace` 缺 `.gitignore`（#12）会把书稿正文纳入版本库。
- **建议优先级**：先修 🔴（已临时修复）→ 重排 `sync` 先体检后落盘并保证失败可回滚 → 封堵空提案 → 补 `.gitignore` → 再统一章号口径与文档。

---

*（本清单中的"已验证"条目均已通过运行复现；两处 🔴 的临时修复见 `engine/pack.py` 与 `engine/checks.py` 的相对改动。）*
