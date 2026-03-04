# stress.py - 自动化对拍脚本
# 使用方法：在题目根目录（开发阶段）运行 python stress.py
# 注意：对拍测试使用小数据（N <= 100），以确保暴力解法能够快速运行
import os
import sys
import subprocess
import io

# 修复 Windows 控制台中文乱码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def log(msg):
    """日志输出（无颜色，避免兼容性问题）"""
    print(msg, flush=True)

def compile_cpp(name):
    """编译 C++ 源文件"""
    log(f"正在编译 {name}.cpp...")
    if os.system(f"g++ -std=c++2c -O2 {name}.cpp -o {name}.exe") != 0:
        log(f"[错误] 编译 {name}.cpp 失败")
        sys.exit(1)

def main():
    # 编译所有源文件（包括验证器）
    compile_cpp("gen")
    compile_cpp("val")
    compile_cpp("sol")
    compile_cpp("brute")

    TRIALS = 1000
    log(f"开始对拍测试，共 {TRIALS} 轮（使用小数据）...")

    for i in range(1, TRIALS + 1):
        # 1. 生成小数据（类型1=小数据，N范围1-50，单组测试）
        with open("input.txt", "w") as f:
            subprocess.call([".\\gen.exe", str(i), "1", "1", "50", "1", "1"], stdout=f)

        # 2. 验证数据格式
        with open("input.txt", "r") as f:
            if subprocess.call([".\\val.exe"], stdin=f) != 0:
                log(f"[错误] 验证器在种子 {i} 上失败")
                sys.exit(1)

        # 3. 运行标准解法
        with open("input.txt", "r") as f_in, open("sol.out", "w") as f_out:
            subprocess.call([".\\sol.exe"], stdin=f_in, stdout=f_out)

        # 4. 运行暴力解法
        with open("input.txt", "r") as f_in, open("brute.out", "w") as f_out:
            subprocess.call([".\\brute.exe"], stdin=f_in, stdout=f_out)

        # 5. 比较结果
        with open("sol.out", "r") as f1, open("brute.out", "r") as f2:
            sol_output = f1.read().strip()
            brute_output = f2.read().strip()
            
            if sol_output != brute_output:
                log(f"[错误] 第 {i} 轮答案不一致！")
                log("输入数据已保存在 input.txt")
                log(f"标准解法: {sol_output}")
                log(f"暴力解法: {brute_output}")
                sys.exit(1)
        
        if i % 100 == 0:
            log(f"已通过 {i} 轮测试...")

    log(f"[成功] 全部 {TRIALS} 轮测试通过！")

if __name__ == "__main__":
    main()
