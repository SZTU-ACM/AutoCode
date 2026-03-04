# setup_dev.py - 设置开发阶段环境
# 将 Polygon 格式的文件复制到开发阶段的根目录结构
import shutil
import os

def setup():
    """将 Polygon 格式文件复制到根目录，用于开发测试"""
    
    # 从 files/ 复制
    files_to_copy = [
        ("files/gen.cpp", "gen.cpp"),
        ("files/val.cpp", "val.cpp"),
        ("files/testlib.h", "testlib.h"),
    ]
    
    # 从 solutions/ 复制
    files_to_copy += [
        ("solutions/sol.cpp", "sol.cpp"),
        ("solutions/brute.cpp", "brute.cpp"),
    ]
    
    # 从 statements/ 复制
    files_to_copy += [
        ("statements/README.md", "README.md"),
    ]
    
    # 从 scripts/ 复制
    files_to_copy += [
        ("scripts/stress.py", "stress.py"),
    ]
    
    for src, dst in files_to_copy:
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"已复制: {src} -> {dst}")
        else:
            print(f"警告: {src} 不存在")
    
    print("\n开发环境设置完成！现在可以运行 python stress.py 进行测试。")

if __name__ == "__main__":
    setup()
