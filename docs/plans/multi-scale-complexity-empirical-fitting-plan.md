# 多规模阶梯数据采样与经验复杂度拟合执行计划

## 一、概述
本计划依据 [docs/designs/multi-scale-complexity-empirical-fitting-design.md](file:///home/cvm-204/AutoCode/docs/designs/multi-scale-complexity-empirical-fitting-design.md)，实施对 AutoCode 复杂度与算法分析体系的全面重构。
移除原有的脆弱正则表达式静态分析，建立“大语言模型声明理论复杂度 + 底层工具链多规模物理实测与对数线性倍率拟合”的确定性实证架构。

---

## 二、任务分解与实施步骤

### 任务 1：经验倍率拟合分析器（EmpiricalRatioAnalyzer）与数学单元测试
- **目标文件**：
  - 新建 `src/autocode_mcp/utils/ratio_analyzer.py`
  - 新建 `tests/test_tools/test_empirical_ratio_analyzer.py`
- **核心逻辑**：
  1. 动态理论期望倍率计算函数：根据任意给定的两测点规模 $N_a, N_b$ 动态求取 $R_{\text{expected}} = f(N_b) / f(N_a)$。
  2. 对数线性回归拟合模型：计算 $\ln(T_{\text{calc}}) = \alpha \ln N + \beta$ 的幂指数 $\alpha$ 与判定系数 $R^2$。
  3. 容差检验与复杂度判定函数：比对实测幂指数 $\alpha$ 与观测倍率，输出结构化判定结果（`verified` 或 `ratio_mismatch`）。
- **验证命令**：
  `uv run pytest tests/test_tools/test_empirical_ratio_analyzer.py -v`

---

### 任务 2：自适应多规模阶梯数据采样器（MultiScaleSampler）与单元测试
- **目标文件**：
  - 新建 `src/autocode_mcp/utils/scale_sampler.py`
  - 新建 `tests/test_tools/test_multi_scale_sampler.py`
- **核心逻辑**：
  1. 自适应采样点计算：依据 $N_{\max}$ 与时间复杂度类别自适应计算 5 个单调递增采样点。杜绝 $N_{\max} \le 30$ 时的数值倒挂。
  2. 生成器调用参数规范化：支持位置参数、命名参数与多测参数注入。
  3. 多测（$T$ 组数据）极端数据分布生成逻辑（大 $T$ 小 $N$ 与小 $T$ 大 $N$）。
- **验证命令**：
  `uv run pytest tests/test_tools/test_multi_scale_sampler.py -v`

---

### 任务 3：纯 CPU 耗时采集与环境底噪扣除监控器（DynamicExecutionMonitor）
- **目标文件**：
  - 新建 `src/autocode_mcp/utils/execution_monitor.py`
  - 新建 `tests/test_utils/test_execution_monitor.py`
- **核心逻辑**：
  1. 跨平台纯 CPU 耗时采集：Linux 环境使用 `getrusage` / `/proc/[pid]/stat`；Windows 环境调用 `GetProcessTimes` 并配合 `timeBeginPeriod(1)`。
  2. 空操作基准程序（Dummy I/O Runner）测量环境底噪与启动耗时 $T_0$，计算纯算法计算耗时 $T_{\text{calc}} = \max(0.1, T_{\text{measured}} - T_0)$。
  3. 交互题双向匿名管道并发调度与独立 CPU 耗时统计。
  4. 进程树生命周期管控：Linux 会话隔离与 `os.killpg`；Windows Job Object 内核清理。
- **验证命令**：
  `uv run pytest tests/test_utils/test_execution_monitor.py -v`

---

### 任务 4：工具层整合与脆弱正则清理
- **目标文件**：
  - 修改 `src/autocode_mcp/tools/complexity.py`：清理 `analyze_loop_complexity` 与 `detect_algorithm_patterns` 中的正则表达式猜测试图；接入 `MultiScaleSampler`、`DynamicExecutionMonitor` 与 `EmpiricalRatioAnalyzer`。
  - 修改 `src/autocode_mcp/tools/solution_audit.py`：在 `solution_audit_std` 与 `solution_audit_brute` 中支持 `claimed_complexity` 归一化输入与多规模经验拟合结果嵌入。
  - 修改 `src/autocode_mcp/tools/audit.py`：在全量审计中将经验拟合指标纳入质量信号，失败时向 `blocking_issues` 与 `next_actions` 追加明确指引。
- **两阶段时序控制**：
  在解法审计（步骤 4）时若 `files/gen` 未生成，标记为待实测；在生成器构建后及步骤 10 与步骤 11 自动执行完整多规模经验验证。
- **验证命令**：
  `uv run pytest tests/test_tools/test_complexity.py tests/test_tools/test_solution_audit.py -v`

---

### 任务 5：端到端集成测试与小常数二次方算法证伪验证
- **目标文件**：
  - 新建 `tests/test_integration/test_complexity_empirical_e2e.py`
- **核心测试用例**：
  1. 线性标答（单调队列）在 $N=10^5$ 规模下通过验证并输出完整实测证据卡片。
  2. 声明 $O(N)$ 但实现小常数 $O(N^2)$ 的伪装算法在 $N=10^4$ 规模下被准确识别为二次方增长并就地阻断。
  3. 交互题双向管道执行与纯 CPU 时间采集准确性测试。
  4. 生成器参数缺失时的极限单点自适应回退测试。
- **验证命令**：
  `uv run pytest tests/test_integration/test_complexity_empirical_e2e.py -v`

---

### 任务 6：代码规范与全量回归测试
- **静态代码检查**：`uv run ruff check .`
- **类型系统检查**：`uv run mypy src/`
- **全量测试套件执行**：`uv run pytest tests/ -q`
