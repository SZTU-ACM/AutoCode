# AutoCode MCP Server

[![PyPI version](https://badge.fury.io/py/autocode-mcp.svg)](https://badge.fury.io/py/autocode-mcp)
[![Python](https://img.shields.io/pypi/pyversions/autocode-mcp.svg)](https://pypi.org/project/autocode-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/Protocol-MCP-blue.svg)](https://modelcontextprotocol.io/)

**An MCP Server for competitive programming problem creation, implementing the Validator-Generator-Checker framework from the AutoCode paper.**

AutoCode MCP Server provides 15 atomic tools that enable AI assistants to create, validate, and test competitive programming problems. It handles compilation, execution, stress testing, and test data generation—letting the AI focus on problem design and solution logic.

[中文文档](README_CN.md)

## Features

- **Validator-Generator-Checker Framework** — Automated validation of input correctness, multi-strategy test generation, and output verification based on the AutoCode paper
- **15 Atomic Tools** — File operations, solution building, stress testing, validator/generator/checker construction, and more
- **testlib.h Support** — Full integration with the competitive programming standard library for validators, generators, and checkers
- **Multi-Strategy Generation** — Four generation strategies: tiny (exhaustive), random, extreme (edge cases), and TLE-inducing
- **Stress Testing** — Automated comparison between optimal and brute-force solutions with configurable trial counts
- **MCP Protocol** — Native support for Claude Code, Cursor, and other MCP-compatible AI tools
- **Execution Control** — Timeout control, memory limits (Linux), and temporary directory isolation (local trusted environments only)
- **Polygon Packaging** — Export problems in Polygon format for Codeforces-style platforms

## Installation

### Claude Code Plugin (Recommended)

This repository is structured as an official Claude Code plugin:

- [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json)
- [`settings.json`](settings.json)
- [`agents/autocode-workflow.md`](agents/autocode-workflow.md)
- [`hooks/hooks.json`](hooks/hooks.json)
- [`.mcp.json`](.mcp.json)

The plugin does not only expose an MCP server. It also ships:

- a default workflow agent
- automatic workflow skills
- enforcement hooks that block skipped AutoCode steps

For a personal third-party marketplace setup, use the sibling repository at `../autocode-marketplace`. That marketplace is configured for remote GitHub-based installation of this plugin, not official marketplace publication.

When you are ready to distribute it, Anthropic’s docs indicate plugin distribution goes through a marketplace, and official marketplace submission is done via the Claude.ai or Console submission forms.

```bash
claude mcp add autocode -- uvx autocode-mcp
```

### From PyPI (Recommended)

```bash
pip install autocode-mcp
```

### Using uv

```bash
uv tool install autocode-mcp
```

### From Source

```bash
git clone https://github.com/SummerOneTwo/AutoCode.git
cd AutoCode
uv sync
```

### Prerequisites

- **Python 3.10+**
- **g++ compiler** with C++20 support (GCC 10+ recommended)
- **testlib.h** (included in templates/)

Verify your setup:

```bash
# Check Python version
python --version

# Check g++ version
g++ --version

# Run tests
uv run pytest tests/ -v
```

## Quick Start

### 1. Install the Plugin

Recommended:

- Install this repository as a Claude Code plugin.
- For local development, test it with `claude --plugin-dir .`.
- For personal marketplace usage, add the sibling marketplace and install from it:

```bash
claude plugin marketplace add ../autocode-marketplace
claude plugin install autocode-mcp@autocode-marketplace
```

Note: marketplace installs pull from `SummerOneTwo/AutoCode`, so push plugin changes before testing remote installation.

Fallback for direct Claude Code MCP setup:

```bash
claude mcp add autocode -- uvx autocode-mcp
```

Project-local alternative:

```json
{
  "mcpServers": {
    "autocode": {
      "command": "autocode-mcp"
    }
  }
}
```

### 2. Create Your First Problem

In Claude Code, simply ask:

> "Create a competitive programming problem: Given two integers A and B, output their sum."

Claude will use AutoCode tools to:
1. Generate problem statement
2. Implement solutions (optimal + brute force)
3. Build validator and generator
4. Run stress tests
5. Generate final test data

### 3. Manual Tool Usage

You can also call tools directly:

```python
# Build a solution
solution_build(
    problem_dir="problems/ab",
    solution_type="sol",
    code="#include <iostream>\nint main() { int a, b; std::cin >> a >> b; std::cout << a + b; }"
)

# Run stress test
stress_test_run(problem_dir="problems/ab", trials=100)
```

## Client Setup

### Transport & Compatibility

**Recommended**: Install as a Claude Code plugin.

**Current Support**: Local stdio transport only. The server communicates via standard input/output streams and is designed for local trusted environments.

| Client | Status | Notes |
|--------|--------|-------|
| Claude Code | ✅ Verified | Primary development environment |
| Cursor | ⚠️ Config provided | Not yet tested end-to-end |
| OpenCode | ⚠️ Config provided | Not yet tested end-to-end |

**Not Supported**: HTTP/SSE transport, remote connections, or multi-tenant environments.

### Claude Code Plugin

Recommended plugin files:

- [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json)
- [`settings.json`](settings.json)
- [`agents/autocode-workflow.md`](agents/autocode-workflow.md)
- [`hooks/hooks.json`](hooks/hooks.json)

Direct MCP fallback:

```bash
claude mcp add autocode -- uvx autocode-mcp
```

Manual config:

```json
{
  "mcpServers": {
    "autocode": {
      "command": "autocode-mcp"
    }
  }
}
```

### Other MCP Clients

The same MCP server also works with other local MCP clients, but the workflow-enforcement agent and hooks are Claude Code plugin features.

### Cursor

Add to your Cursor settings (Settings → MCP):

```json
{
  "mcp": {
    "servers": {
      "autocode": {
        "command": "autocode-mcp"
      }
    }
  }
}
```

### OpenCode

Edit `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "autocode": {
      "type": "local",
      "command": ["autocode-mcp"],
      "enabled": true
    }
  }
}
```

Or use `uvx` without pre-installation:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "autocode": {
      "type": "local",
      "command": ["uvx", "autocode-mcp"],
      "enabled": true
    }
  }
}
```

### From Source (Development)

For development or custom installations:

```json
{
  "mcpServers": {
    "autocode": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/autocode-mcp", "autocode-mcp"]
    }
  }
}
```

### Verify Installation

After configuration, restart your MCP client and check that tools are available. You should see 15 tools available.

## Tools Reference

AutoCode provides 15 atomic tools organized into 7 groups. All tools return a unified format:

```json
{
  "success": true,
  "error": null,
  "data": { ... }
}
```

### File Operations

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `file_save` | Save content to a file | `path`, `content` |
| `file_read` | Read file content | `path` |

### Solution Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `solution_build` | Compile solution code | `problem_dir`, `solution_type` ("sol"/"brute"), `code` |
| `solution_run` | Execute compiled solution | `problem_dir`, `solution_type`, `input_data`, `timeout` |

### Validator Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `validator_build` | Build and test validator | `problem_dir`, `code`, `test_cases` |
| `validator_select` | Select best validator from candidates | `candidates` |

### Generator Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `generator_build` | Compile generator | `problem_dir`, `code` |
| `generator_run` | Generate test inputs | `problem_dir`, `strategies`, `test_count`, `validator_path` |

### Checker Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `checker_build` | Build output checker | `problem_dir`, `code`, `test_scenarios` |

### Interactor Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `interactor_build` | Build interactor for interactive problems | `problem_dir`, `code`, `reference_solution_path`, `mutant_solutions` |

### Stress Testing

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `stress_test_run` | Compare sol vs brute outputs | `problem_dir`, `trials`, `n_max`, `timeout` |

### Problem Management

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `problem_create` | Initialize problem directory | `problem_dir`, `problem_name` |
| `problem_generate_tests` | Generate final test data | `problem_dir`, `test_count` |
| `problem_pack_polygon` | Package for Polygon platform | `problem_dir`, `time_limit`, `memory_limit` |

## Workflow Tutorial: A+B Problem

This tutorial walks through creating a simple A+B problem using AutoCode tools.

### Step 1: Initialize Problem

```python
problem_create(
    problem_dir="problems/ab",
    problem_name="A + B"
)
```

### Step 2: Implement Solutions

**Optimal Solution (sol.cpp):**
```cpp
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
```

**Brute Force (brute.cpp):**
```cpp
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    // Same as optimal for A+B, but could be slower for complex problems
    std::cout << a + b << std::endl;
    return 0;
}
```

Build both:
```python
solution_build(problem_dir="problems/ab", solution_type="sol", code="...")
solution_build(problem_dir="problems/ab", solution_type="brute", code="...")
```

### Step 3: Build Validator

```cpp
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    int a = inf.readInt(-1000, 1000, "a");
    inf.readSpace();
    int b = inf.readInt(-1000, 1000, "b");
    inf.readEoln();
    inf.readEof();
    return 0;
}
```

Build with test cases:
```python
validator_build(
    problem_dir="problems/ab",
    code="...",
    test_cases=[
        {"input": "1 2\n", "expected_valid": True},
        {"input": "0 0\n", "expected_valid": True},
        {"input": "-1000 1000\n", "expected_valid": True},
        {"input": "1001 0\n", "expected_valid": False},  # out of range
        {"input": "1 2 3\n", "expected_valid": False},   # extra number
    ]
)
```

### Step 4: Build Generator

```cpp
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    rnd.setSeed(seed);
    
    int a = rnd.next(-1000, 1000);
    int b = rnd.next(-1000, 1000);
    std::cout << a << " " << b << std::endl;
    return 0;
}
```

Build and run:
```python
generator_build(problem_dir="problems/ab", code="...")

generator_run(
    problem_dir="problems/ab",
    strategies=["random", "extreme"],
    test_count=20,
    validator_path="problems/ab/val.exe"
)
```

### Step 5: Stress Test

```python
stress_test_run(
    problem_dir="problems/ab",
    trials=1000,
    n_max=100,
    timeout=30
)
```

Expected output:
```
All 1000 rounds passed
```

### Step 6: Generate Final Tests

```python
problem_generate_tests(
    problem_dir="problems/ab",
    test_count=50
)
```

### Step 7: Package for Polygon

```python
problem_pack_polygon(
    problem_dir="problems/ab",
    time_limit=1,
    memory_limit=256
)
```

## Architecture

### Validator-Generator-Checker Framework

```
┌─────────────────┐
│  Problem Design │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│  Solution Build │────►│  Validator   │ Verify input constraints
│  (sol + brute)  │     └──────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│  Generator      │────►│  Stress Test │ Compare sol vs brute
│  Multi-strategy │     └──────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Checker        │ Verify output correctness
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Polygon Pack   │ Export for platforms
└─────────────────┘
```

### Design Principles

1. **Tool-Only, No LLM** — Server provides compilation, execution, and validation. All code generation is done by the client LLM.

2. **Stateless** — Each tool call is independent. State is managed via `problem_dir` parameter.

3. **Unified Return Format** — All tools return `{success, error, data}` for consistent error handling.

4. **Execution Control** — Timeout control, memory limits (Linux via prlimit), and temporary directory isolation.

### Security Boundaries

⚠️ **Important: This tool is designed for local trusted environments only**

#### File Operations

- **With `problem_dir` parameter**: `file_read` and `file_save` restrict access to paths within the specified directory
- **Without `problem_dir` parameter**: These tools can read/write **any arbitrary path** on the filesystem
- **Recommendation**: Always specify `problem_dir` when calling file operations to limit scope

#### Code Execution

- Compiles and executes AI-generated C++ code with only time/memory limits
- No sandbox isolation (uses `prlimit` on Linux for memory limits only)
- **Risk**: Malformed or malicious code could potentially affect the system

#### Use Cases

✅ **Suitable For**:
- Local development machines
- Competitive programming problem creation
- AI-assisted coding in trusted environments
- Personal workstations with regular backups

❌ **Not Suitable For**:
- Multi-tenant environments
- Untrusted code execution
- Production-grade code execution platforms
- Shared servers without isolation

#### Mitigation Strategies

For stronger isolation, consider:
- Running inside a Docker container
- Using a virtual machine
- Restricting filesystem permissions at the OS level
- Running as a non-privileged user

### Generation Strategies

| Strategy | Type Code | Purpose |
|----------|-----------|---------|
| `tiny` | 1 | Small exhaustive tests (N ≤ 10) |
| `random` | 2 | Random data within constraints |
| `extreme` | 3 | Edge cases: overflow, precision, hash collisions |
| `tle` | 4 | TLE-inducing data for performance testing |

### File Structure

```
problems/your-problem/
├── files/
│   ├── testlib.h       # Competitive programming standard library
│   ├── gen.cpp         # Test generator
│   ├── val.cpp         # Input validator
│   ├── checker.cpp     # Output checker (optional)
│   └── interactor.cpp  # Interactor (for interactive problems)
├── solutions/
│   ├── sol.cpp         # Optimal solution
│   └── brute.cpp       # Brute force (for validation)
├── statements/
│   └── README.md       # Problem statement
├── tests/
│   ├── 01.in           # Test input
│   ├── 01.ans          # Expected output
│   └── ...
└── problem.xml         # Polygon configuration
```

## Development

### Setup

```bash
git clone https://github.com/SummerOneTwo/AutoCode.git
cd AutoCode
uv sync
```

### Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=src/autocode_mcp --cov-report=html

# Run specific test file
uv run pytest tests/test_compiler.py -v
```

### Code Quality

```bash
# Linting
uv run ruff check .

# Type checking
uv run mypy src/

# Format
uv run ruff format .
```

### Project Structure

```
autocode-mcp/
├── src/autocode_mcp/
│   ├── tools/           # MCP tool implementations
│   │   ├── base.py      # Tool base class
│   │   ├── solution.py  # Solution tools
│   │   ├── validator.py # Validator tools
│   │   ├── generator.py # Generator tools
│   │   ├── checker.py   # Checker tools
│   │   ├── stress_test.py
│   │   └── ...
│   ├── utils/
│   │   ├── compiler.py  # C++ compilation utilities
│   │   └── platform.py  # Platform-specific helpers
│   ├── prompts/         # Workflow prompt templates
│   ├── resources/       # Template resources
│   └── server.py        # MCP server entry point
├── templates/           # C++ templates (testlib.h, etc.)
├── tests/               # Test suite
└── pyproject.toml
```

### Adding New Tools

1. Create a new file in `src/autocode_mcp/tools/`
2. Inherit from `Tool` base class
3. Implement `name`, `description`, `input_schema`, and `execute()`
4. Register in `server.py`
5. Add tests in `tests/`

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues and solutions.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- Based on the paper ["AutoCode: LLMs as Problem Setters for Competitive Programming"](https://arxiv.org/abs/2510.12803)
- Uses [testlib.h](https://github.com/MikeMirzayanov/testlib) for competitive programming utilities
- Built on the [Model Context Protocol](https://modelcontextprotocol.io/)

## Links

- [Documentation](https://github.com/SummerOneTwo/AutoCode#readme)
- [PyPI](https://pypi.org/project/autocode-mcp/)
- [GitHub](https://github.com/SummerOneTwo/AutoCode)
- [Issue Tracker](https://github.com/SummerOneTwo/AutoCode/issues)
