# gen_tests.py - 测试数据生成脚本
# 用于生成完整的测试数据集（20 组测试）

import os
import sys
import subprocess
import shutil

def check_working_directory():
    """检查工作目录是否正确"""
    required = ["files", "solutions"]
    for d in required:
        if not os.path.exists(d):
            print(f"错误：找不到 {d}/ 目录，请在题目根目录下运行此脚本")
            sys.exit(1)

def main():
    check_working_directory()
    
    # 清空并重建 tests 目录
    if os.path.exists("tests"):
        shutil.rmtree("tests")
    os.makedirs("tests")

    gen_exe = os.path.join("files", "gen.exe")
    sol_exe = os.path.join("solutions", "sol.exe")

    # 生成器用法: gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>
    # type: 1=小数据, 2=随机, 3=大值, 4=边界, 5=反hack

    commands = []
    
    # 1. 小数据 (测试 01-03): N <= 10
    commands.append([gen_exe, "1", "1", "10", "1", "3"])
    commands.append([gen_exe, "1", "1", "10", "1", "3"])
    commands.append([gen_exe, "1", "1", "10", "1", "3"])

    # 2. 随机数据 (测试 04-10): N <= 10000
    commands.append([gen_exe, "2", "10", "100", "1", "3"])
    commands.append([gen_exe, "2", "100", "1000", "1", "3"])
    commands.append([gen_exe, "2", "1000", "5000", "1", "3"])
    commands.append([gen_exe, "2", "5000", "10000", "1", "3"])
    commands.append([gen_exe, "2", "100", "10000", "2", "5"])
    commands.append([gen_exe, "2", "10", "10000", "1", "3"])
    commands.append([gen_exe, "2", "1000", "10000", "1", "2"])

    # 3. 大数据 (测试 11-15): N 接近 200000
    commands.append([gen_exe, "3", "100000", "200000", "1", "1"])
    commands.append([gen_exe, "3", "150000", "200000", "1", "1"])
    commands.append([gen_exe, "3", "190000", "200000", "1", "1"])
    commands.append([gen_exe, "3", "199000", "200000", "1", "1"])
    commands.append([gen_exe, "3", "200000", "200000", "1", "1"])

    # 4. 边界数据 (测试 16-18)
    commands.append([gen_exe, "4", "10", "50", "1", "3"])
    commands.append([gen_exe, "4", "10", "100", "1", "3"])
    commands.append([gen_exe, "4", "50", "200", "1", "3"])

    # 5. 反hack数据 (测试 19-20)
    commands.append([gen_exe, "5", "50", "200", "1", "3"])
    commands.append([gen_exe, "5", "100", "500", "1", "3"])

    for i, cmd in enumerate(commands, 1):
        test_file = os.path.join("tests", f"{i:02d}.in")
        ans_file = os.path.join("tests", f"{i:02d}.ans")
        
        print(f"正在生成测试 {i}...")
        with open(test_file, "w") as f:
            # 构建参数: [gen_exe, seed, type, n_min, n_max, t_min, t_max]
            final_args = [cmd[0], str(i)] + cmd[1:]
            subprocess.run(final_args, stdout=f, check=True)
            
        print(f"正在生成测试 {i} 的答案...")
        with open(test_file, "r") as f_in, open(ans_file, "w") as f_out:
            subprocess.run([sol_exe], stdin=f_in, stdout=f_out, check=True)

    print("完成！已生成 20 组测试数据。")

if __name__ == "__main__":
    main()
