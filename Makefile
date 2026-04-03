.PHONY: $(MAKECMDGOALS)

# 默认目标
help:
	@echo "Available targets:"
	@echo "  setup        - 安装依赖"
	@echo "  test         - 运行测试"
	@echo "  test-cov     - 运行测试并生成覆盖率报告"
	@echo "  lint         - 运行 ruff lint 检查"
	@echo "  format       - 格式化代码"
	@echo "  typecheck    - 运行 mypy 类型检查"
	@echo "  check        - 运行所有检查 (lint + typecheck + test)"
	@echo "  clean        - 清理缓存和临时文件"
	@echo "  docs         - 启动文档服务器"

# 安装依赖
setup:
	uv sync --all-extras

# 运行测试
test:
	uv run pytest tests/ -v

# 运行测试并生成覆盖率报告
test-cov:
	uv run pytest tests/ --cov=src/autocode_mcp --cov-report=html --cov-report=term

# 运行 ruff lint 检查
lint:
	uv run ruff check src/ tests/

# 格式化代码
format:
	uv run ruff format src/ tests/

# 运行 mypy 类型检查
typecheck:
	uv run mypy src/

# 运行所有检查
check: lint typecheck test

# 清理缓存和临时文件（跨平台兼容）
clean:
	uv run python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', '.coverage'] if pathlib.Path(p).exists()]"

# 启动文档服务器 (如果有 mkdocs)
docs:
	uv run mkdocs serve
