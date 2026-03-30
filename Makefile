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

# 清理缓存和临时文件
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage 2>/dev/null || true

# 启动文档服务器 (如果有 mkdocs)
docs:
	uv run mkdocs serve
