# check_env.py - 环境检查脚本
# 用于验证题目所需文件是否存在

import os
import subprocess

def check():
    """检查题目环境"""
    
    print("检查 files/ 目录:")
    if os.path.exists("files"):
        for f in os.listdir("files"):
            print(f"  - {f}")
    else:
        print("  错误: files/ 目录不存在!")

    print("\n检查 solutions/ 目录:")
    if os.path.exists("solutions"):
        for f in os.listdir("solutions"):
            print(f"  - {f}")
    else:
        print("  错误: solutions/ 目录不存在!")

    print("\n检查 statements/ 目录:")
    if os.path.exists("statements"):
        for f in os.listdir("statements"):
            print(f"  - {f}")
    else:
        print("  错误: statements/ 目录不存在!")

    print("\n尝试运行 gen.exe:")
    if os.path.exists("files/gen.exe"):
        try:
            result = subprocess.run([".\\files\\gen.exe", "1"], 
                                    capture_output=True, text=True, timeout=5)
            print("  gen.exe 运行成功")
            output = result.stdout or ""
            print(f"  输出预览: {output}")
        except Exception as e:
            print(f"  运行 gen.exe 失败: {e}")
    else:
        print("  files/gen.exe 不存在（需要先编译）")

if __name__ == "__main__":
    check()
