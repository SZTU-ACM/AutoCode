# pack_polygon.py - 打包成 Polygon 格式
# 将开发阶段的文件整理到 Polygon 标准目录结构
import os
import shutil
import io
import sys

# 修复 Windows 控制台中文乱码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def log(msg):
    print(msg, flush=True)

def pack():
    """将开发阶段文件打包到 Polygon 格式"""
    
    log("开始整理为 Polygon 格式...")
    
    # 1. 同步 files/ 目录（testlib.h, gen.cpp, val.cpp）
    log("同步 files/ 目录...")
    if not os.path.exists("files"):
        os.makedirs("files")
    
    for src in ["testlib.h", "gen.cpp", "val.cpp"]:
        if os.path.exists(src):
            shutil.copy2(src, f"files/{src}")
            log(f"  复制: {src} -> files/{src}")
    
    # 2. 同步 solutions/ 目录（sol.cpp, brute.cpp）
    log("同步 solutions/ 目录...")
    if not os.path.exists("solutions"):
        os.makedirs("solutions")
    
    for src in ["sol.cpp", "brute.cpp"]:
        if os.path.exists(src):
            shutil.copy2(src, f"solutions/{src}")
            log(f"  复制: {src} -> solutions/{src}")
    
    # 3. 同步 statements/ 目录（README.md）
    log("同步 statements/ 目录...")
    if not os.path.exists("statements"):
        os.makedirs("statements")
    
    if os.path.exists("README.md"):
        shutil.copy2("README.md", "statements/README.md")
        log("  复制: README.md -> statements/README.md")
    
    # 4. 同步 scripts/ 目录
    log("同步 scripts/ 目录...")
    if not os.path.exists("scripts"):
        os.makedirs("scripts")
    
    if os.path.exists("stress.py"):
        shutil.copy2("stress.py", "scripts/stress.py")
        log("  复制: stress.py -> scripts/stress.py")
    
    # 5. 清理根目录下的开发阶段文件
    log("清理根目录开发阶段文件...")
    dev_files = [
        "testlib.h", "gen.cpp", "val.cpp", "sol.cpp", "brute.cpp",
        "gen.exe", "val.exe", "sol.exe", "brute.exe",
        "input.txt", "sol.out", "brute.out",
        "README.md", "stress.py", "run_stress_test.bat"
    ]
    
    for f in dev_files:
        if os.path.exists(f):
            os.remove(f)
            log(f"  删除: {f}")
    
    log("")
    log("[成功] Polygon 格式整理完成！")
    log("")
    log("目录结构:")
    log("  files/       <- testlib.h, gen.cpp, val.cpp")
    log("  solutions/   <- sol.cpp, brute.cpp")
    log("  statements/  <- README.md")
    log("  scripts/     <- stress.py")
    log("  tests/       <- 测试数据")

if __name__ == "__main__":
    pack()
