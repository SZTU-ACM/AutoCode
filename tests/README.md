# 测试分层说明

本项目采用分层测试策略，确保从单元到端到端的全面覆盖。

## 测试层级

```
┌─────────────────────────────────────────────────────────────┐
│                    L4: 打包产物测试                          │
│  test_packaging_smoke.py                                    │
│  验证 wheel 安装后 console script 正常工作                   │
│  运行时机：uv build 后，在独立虚拟环境中                      │
├─────────────────────────────────────────────────────────────┤
│                    L3: 端到端 MCP 测试                       │
│  test_e2e_mcp.py                                            │
│  通过 stdio 启动真实 MCP Server 进程，验证协议兼容性          │
│  运行时机：CI 常规测试（源码环境）                            │
├─────────────────────────────────────────────────────────────┤
│                    L2: 集成测试                              │
│  test_server.py, test_compiler.py, test_*.py               │
│  测试模块间交互、工具链集成                                   │
│  运行时机：CI 常规测试                                       │
├─────────────────────────────────────────────────────────────┤
│                    L1: 单元测试                              │
│  test_cache.py, test_mixins.py, test_resource_limit.py,    │
│  测试独立函数和类的行为                                       │
│  运行时机：CI 常规测试                                       │
└─────────────────────────────────────────────────────────────┘
```

## 测试文件职责

### L1: 单元测试

| 文件 | 职责 |
|------|------|
| `test_cache.py` | 测试编译缓存 |
| `test_mixins.py` | 测试工具 mixin 行为 |
| `test_resource_limit.py` | 测试资源限制工具 |
| `test_win_job.py` | 测试 Windows Job Object |

### L2: 集成测试

| 文件 | 职责 |
|------|------|
| `test_server.py` | 测试 MCP Server 工具注册和调用 |
| `test_compiler.py` | 测试 C++ 编译器集成 |
| `test_packaging.py` | 测试打包配置、模板访问、MCP 类型 |

### L3: 端到端 MCP 测试

| 文件 | 职责 |
|------|------|
| `test_e2e_mcp.py` | 真实 MCP 协议握手和工具调用 |

### L4: 打包产物测试

| 文件 | 职责 |
|------|------|
| `test_packaging_smoke.py` | 验证 wheel 安装后 console script |

## CI 测试流程

```yaml
# 1. 单元测试 + 集成测试（多 Python 版本）
test-unit:
  - uv run pytest tests/ -v -m "not integration"

# 2. 集成测试（标记为 integration）
test-integration:
  - uv run pytest tests/ -v -m "integration"

# 3. 打包产物测试（uv build 后）
test-packaging:
  - uv build
  - uv pip install dist/*.whl
  - pytest tests/test_packaging_smoke.py -v -m "packaging"
```

## 测试标记

| 标记 | 用途 | 示例 |
|------|------|------|
| `@pytest.mark.integration` | 集成测试 | 需要 g++ 或外部依赖 |
| `@pytest.mark.packaging` | 打包测试 | 需要从 wheel 安装 |

## 运行测试

```bash
# 运行所有单元测试和集成测试
uv run pytest tests/ -v

# 只运行单元测试
uv run pytest tests/ -v -m "not integration"

# 只运行集成测试
uv run pytest tests/ -v -m "integration"

# 运行端到端 MCP 测试
uv run pytest tests/test_e2e_mcp.py -v

# 运行打包产物测试（需要先安装 wheel）
pytest tests/test_packaging_smoke.py -v -m "packaging"
```

## 测试覆盖目标

- **L1 单元测试**: 覆盖核心逻辑，快速反馈
- **L2 集成测试**: 覆盖模块交互，验证工具链
- **L3 端到端测试**: 覆盖 MCP 协议兼容性
- **L4 打包测试**: 覆盖发布产物可用性
