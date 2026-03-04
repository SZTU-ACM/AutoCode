# cleanup.py - 清理脚本
# 用于清理编译产物和临时文件

import os
import shutil

def cleanup():
    """清理项目中的临时文件和编译产物"""
    
    # 要清理的文件扩展名
    exts_to_remove = ['.exe', '.obj', '.o', '.log', '.out']
    
    # 要清理的特定文件
    files_to_remove = ['input.txt', 'sol.out', 'brute.out']
    
    removed_count = 0
    
    # 遍历目录
    for root, dirs, files in os.walk("."):
        # 跳过 .git 目录
        if ".git" in root:
            continue
        
        for f in files:
            full_path = os.path.join(root, f)
            _, ext = os.path.splitext(f)
            
            if ext in exts_to_remove:
                try:
                    os.remove(full_path)
                    print(f"已删除: {full_path}")
                    removed_count += 1
                except Exception as e:
                    print(f"删除失败 {full_path}: {e}")
            elif f in files_to_remove:
                try:
                    os.remove(full_path)
                    print(f"已删除: {full_path}")
                    removed_count += 1
                except Exception as e:
                    print(f"删除失败 {full_path}: {e}")

    # 删除 tests 目录（测试数据可重新生成）
    if os.path.exists("tests"):
        try:
            shutil.rmtree("tests")
            print("已删除: tests 目录")
            removed_count += 1
        except Exception as e:
            print(f"删除 tests 目录失败: {e}")

    if removed_count == 0:
        print("没有需要清理的文件。")
    else:
        print(f"\n清理完成，共删除 {removed_count} 个文件/目录。")

if __name__ == "__main__":
    cleanup()
