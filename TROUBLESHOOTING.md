# 故障排查指南

本文档提供 AutoCode MCP Server 的常见问题和解决方案。

## 编译问题

### C++ 编译器未找到

**错误信息：**
```
Compiler not found: g++
Compilation failed with error
```

**解决方案：**

1. Windows 平台：
   - 安装 [MinGW-w64](https://www.mingw-w64.org/)
   - 或安装 [MSYS2](https://www.msys2.org/)
   - 确保 `g++` 在 PATH 中

2. Linux/macOS：
   - 安装 `g++`：`sudo apt install g++` 或 `brew install gcc`

3. 验证安装：
   ```bash
   g++ --version
   ```

### C++20 标准不支持（含 RHEL / TencentOS 默认旧版 `g++`）

**错误信息：**
```
error: unrecognized command line option "-std=c++20"
```

**解决方案：**

1. AutoCode 编译器解析器会在 Linux 上自动探测系统 `g++` 主版本：若 `g++ < 10`，会自动搜索 `/opt/rh/gcc-toolset-*` / `/opt/rh/devtoolset-*` 或版本化 `g++-14`/`g++-12`（并优先选择同时附带 `libasan.a` 等 Sanitizer 运行时的完整 toolset）。
2. 如需手动安装或升级：
   ```bash
   gcc --version  # 需要 10.0 或更高
   # RHEL / TencentOS / Rocky: dnf install gcc-toolset-12-gcc-c++ gcc-toolset-12-libasan-devel
   # Debian / Ubuntu: sudo apt install g++
   ```
3. macOS 若默认 Apple Clang 不支持 `<bits/stdc++.h>`，可在 `.autocode/manifest.json` 中显式配置 `"compiler": "g++-14"`（Homebrew GCC）。

### 编译超时

**错误信息：**
```
Compilation timeout after 30s
```

**解决方案：**

1. 检查代码复杂度，可能有模板递归展开
2. 增加 `compile_cpp()` 的 `timeout` 参数
3. 检查是否有头文件循环依赖

## 执行问题

### 二进制文件未找到

**错误信息：**
```
Binary not found: /path/to/binary
```

**解决方案：**

1. 确保已运行 `*_build` 工具
2. 检查文件扩展名：
   - Windows：需要 `.exe`
   - Linux/macOS：无扩展名
3. 验证编译是否成功（检查 `compile_result.success`）

### 执行超时 (TLE)

**错误信息：**
```
Execution timeout after 30s
```

**解决方案：**

1. 增加超时时间
2. 优化算法复杂度
3. 检查是否有死循环或低效代码

### 内存限制与 RSS 检测问题（Linux / macOS / Windows）

**说明：**
- **Linux**：AutoCode 默认优先读取 `/proc/<pid>/status` `VmHWM` 并在开启限额时叠加 `prlimit --as/--data` 与 1ms 自适应峰值采样，避免容器化/父进程场景下 `os.wait4` 继承父进程 RSS 导致误报 MLE；如缺少 `prlimit` 请安装 `util-linux`（`sudo apt install util-linux`）。
- **macOS**：通过子进程 `os.wait4` `ru_maxrss` 获取真实峰值内存；编译缓存命中时会以原子方式写入新 inode，避免 Apple Silicon 上重复覆盖可执行文件触发 Gatekeeper/codesign 校验异常。
- **Windows**：通过 Win32 Job Object 施加进程树内存上限，并将 `STATUS_COMMITMENT_LIMIT`（`0xC000012D` / `0xC0000017`）等退出码自动归类为 MLE。

## 沙箱、可选增强与外部工具

### 沙箱后端不可用（`--sandbox`）

- AutoCode 支持 `--sandbox auto|none|bwrap|nsjail|isolate|apple_container`：
  - 使用 `--sandbox auto` 时会自动探测宿主机可用后端并在缺少二进制时平滑降级为 `none`。
  - 显式指定 `--sandbox bwrap` 等后端但在容器内缺少用户命名空间权限时，AutoCode 会自动探测 `--unshare-user-try` 与 `--proc /proc` 挂载可用性；若宿主策略彻底禁止特权命名空间，可降级为 `--sandbox none`（仅限可信工作区或一次性 GitHub-hosted runner）。

### 可选增强依赖与多语言/排版工具

- **AST 变异与调用图分析**：安装 `uv sync --extra ast`（`tree-sitter` + `tree-sitter-cpp`），缺失时自动回退正则/关键字保守分支。
- **Z3 形式化约束（`constraints.h`）**：安装 `uv sync --extra formal`（`z3-solver`），缺失时跳过 SMT 求解并明确上报状态。
- **性能剖析与 PDF 渲染**：`cachegrind` 依赖 `valgrind`，PDF 编译依赖 `typst`；若未安装 `typst`，可执行 `python -m autocode_mcp.cli.render_pdf <dir> --emit-typ` 仅生成 `.typ` 源码。
- **多语言评测**：`python` 复用当前解释器，`pypy` / `rust` / `java` 分别按需探测 `pypy3` / `rustc` / `javac` 与 `java`。

## 测试问题

### stress_test 失败

**可能原因：**

1. **Generator 失败**：检查 `gen.cpp` 生成逻辑
2. **sol 失败**：检查 `sol.cpp` 代码正确性
3. **brute 失败**：检查 `brute.cpp` 代码正确性
4. **Validator 失败**：检查 `val.cpp` 验证逻辑

**调试步骤：**

1. 运行单轮测试（设置 `trials=1`）
2. 检查失败的输入数据
3. 比较 `sol` 和 `brute` 的输出差异

### Validator 验证不一致

**症状：**
- 预期 `expected_valid=True`，但实际返回非零退出码
- 或相反情况

**解决方案：**

1. 检查 Validator 使用 `quitf(_ok, ...)` 或 `quitf(_wa, ...)`
2. 确保退出码正确：
   - `_ok` → exit 0
   - `_wa` 或其他 → exit 非 0

## 平台特定问题

### Windows 路径问题

**症状：**
```
OSError: [WinError 32] The process cannot access the file
```

**解决方案：**

1. 确保路径使用正斜杠 `/` 或双反斜杠 `\\`
2. 避免使用保留名称（如 `con`, `prn`, `aux`）
3. 检查文件是否被其他进程占用

### Windows 文件权限问题

**错误信息：**
```
PermissionError: [Errno 13] Permission denied
```

**解决方案：**

1. 关闭可能占用文件的程序
2. 以管理员身份运行
3. 检查文件是否设置为只读

## 性能优化

### 测试生成缓慢

**优化建议：**

1. 使用 `test_count` 参数限制测试数量
2. 调整 `max_attempts` 系数（默认 `test_count * 10`）
3. 优化 `gen.cpp` 生成算法复杂度

### 对拍测试缓慢

**优化建议：**

1. 减小 `n_max` 参数（默认 100）
2. 减少 `trials` 轮数
3. 优化 `brute.cpp` 暴力解法复杂度

## MCP Server 问题

### 无法连接 Server

**检查清单：**

1. 确认 `autocode-mcp` 命令可执行
2. 检查 MCP 客户端配置
3. 验证 Python 虚拟环境已激活

### 工具调用失败

**调试步骤：**

1. 查看 Server 日志与 `.autocode/logs/` 中的完整输出
2. 验证 `input_schema` 参数是否正确
3. 检查 `problem_dir` 路径是否存在且有写权限

### Streamable HTTP 传输被拒绝（HTTP 421 / 403）

- 使用 `autocode-mcp --transport http` 时，服务端默认仅绑定 `127.0.0.1:8765` 并启用 Host/Origin DNS Rebinding 防护。
- 若客户端携带非标准 Origin，可通过 `--allowed-origin <origin>` 白名单显式放行（不建议绑定公网网卡）。

## CI 与安全边界

### Pull Request 自动审题（`autocode-audit --standalone`）安全规范

**风险说明：**
`autocode-audit` 会实际编译并运行待测题包中的 C++ 代码（解法、生成器、校验器、交互器等）。如果工作流运行在不受信任的 Pull Request 上，这些源码属于不可信代码执行面。

**强制安全边界：**

1. **仅使用一次性（ephemeral）GitHub-hosted runner**：如 `runs-on: ubuntu-latest`，确保每次运行结束后环境销毁，无状态残留。
2. **严禁使用特权或持久化 `self-hosted` runner**：切勿将不可信 PR 审题调度到内网、宿主机或带特权权限的自托管 runner，以防横向渗透或宿主机持久化污染。
3. **严禁注入敏感 Secrets**：审题 Job 仅保留最小权限 `permissions: { contents: read }`，切勿向构建/审计步骤传入部署密钥、API Token 或发布凭据。
4. **严禁将 `pull_request` 改为 `pull_request_target`**：`pull_request_target` 会在基础分支上下文中运行工作流并暴露仓库级 Secrets 与写权限，结合 C++ 编译执行会导致严重供应链安全漏洞。

## 获取帮助

如果遇到未在本文档中列出的问题：

1. 查看测试用例：`tests/` 目录
2. 检查工具文档：`src/autocode_mcp/tools/` 源代码注释
3. 运行测试验证环境：`pytest tests/ -v`
4. 提交 Issue：[GitHub Issues](https://github.com/SZTU-ACM/AutoCode/issues)
