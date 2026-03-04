import os
import sys
import subprocess
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def log(msg):
    print(msg, flush=True)

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gen_path = os.path.join(base_dir, 'files', 'gen.exe')
    val_path = os.path.join(base_dir, 'files', 'val.exe')
    sol_path = os.path.join(base_dir, 'solutions', 'sol.exe')
    tests_dir = os.path.join(base_dir, 'tests')
    
    # ensure tests directory exists
    os.makedirs(tests_dir, exist_ok=True)
    
    # generator template configurations
    # format: (count, N, type, group_name)
    # type: 0=random tree, 1=star graph, 2=line graph, 3=pure max/min (all 1/0)
    configs = [
        # Small examples
        (1, 4, 0, "sample1"),
        (1, 9, 0, "sample2"),
        # Small manual or random
        (3, 10, 0, "small_random"),
        (2, 10, 1, "small_star"),
        (2, 10, 2, "small_line"),
        # Medium
        (4, 1000, 0, "medium_random"),
        (2, 1000, 1, "medium_star"),
        (2, 1000, 2, "medium_line"),
        # Large (max complexity)
        (4, 200000, 0, "large_random"),
        (4, 200000, 1, "large_star"),
        (4, 200000, 2, "large_line"),
        (2, 200000, 3, "large_corner_pure"),
    ]

    test_idx = 1
    seed = 42

    for count, n, t, group in configs:
        log(f"Generating group: {group} (N={n}, type={t})")
        for _ in range(count):
            in_file = os.path.join(tests_dir, f"{test_idx:02d}.in")
            ans_file = os.path.join(tests_dir, f"{test_idx:02d}.ans")
            
            # Generate .in
            with open(in_file, "w") as f_in:
                subprocess.call([gen_path, str(n), str(t), str(seed)], stdout=f_in)
            
            # Validate .in
            with open(in_file, "r") as f_in:
                if subprocess.call([val_path], stdin=f_in) != 0:
                    log(f"[Error] Validation failed on test {test_idx}")
                    sys.exit(1)
                    
            # Generate .ans
            with open(in_file, "r") as f_in, open(ans_file, "w") as f_ans:
                subprocess.call([sol_path], stdin=f_in, stdout=f_ans)
                
            log(f"  -> Generated test {test_idx:02d}")
            test_idx += 1
            seed += 1

    log(f"[Success] All {test_idx - 1} tests generated safely.")

if __name__ == "__main__":
    main()
