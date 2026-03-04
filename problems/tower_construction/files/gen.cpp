// 积木高塔 - 数据生成器
// 使用 testlib.h 库
// 用法: gen.exe <seed> [type] [n_min] [n_max] [t_min] [t_max]
//   seed:  随机种子（必需，testlib 要求）
//   type:  数据类型（可选，默认 2=随机）
//          1=小数据, 2=随机, 3=大值, 4=边界, 5=反hack
//   n_min, n_max: N 的范围（可选）
//   t_min, t_max: T 的范围（可选）

#include "testlib.h"
#include <iostream>
#include <vector>
#include <string>
#include <algorithm>

using namespace std;

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int type = 2; // 默认：随机数据
    int n_min = 1, n_max = 100; 
    int t_min = 1, t_max = 3; 
    
    if (argc == 2) {
        // 模式：压力测试（仅传入种子，随机选择类型）
        type = rnd.next(1, 5);
        if (type == 1) { n_min = 1; n_max = 10; }        // 小数据
        else if (type == 2) { n_min = 10; n_max = 100; } // 中等数据
        else if (type == 3) { n_min = 100; n_max = 500; }// 较大数据
        else { n_min = 10; n_max = 50; }                 // 其他
    } else {
        // 模式：显式生成（指定参数）
        if (argc > 2) type = atoi(argv[2]);
        if (argc > 4) {
            n_min = atoi(argv[3]);
            n_max = atoi(argv[4]);
        }
        if (argc > 6) {
            t_min = atoi(argv[5]);
            t_max = atoi(argv[6]);
        }
    }
    
    // 确保范围有效
    if (n_min > n_max) swap(n_min, n_max);
    int t_min_def = 1, t_max_def = 3;
    if (argc > 6) { t_min_def = t_min; t_max_def = t_max; }
    
    int t = rnd.next(t_min_def, t_max_def);
    println(t);

    for(int _ = 0; _ < t; ++_) {
        int n = rnd.next(n_min, n_max);
        vector<int> w(n);
        
        if (type == 1) {
            // 类型1：小数据，值范围 [1, 100]
            for(int i = 0; i < n; ++i) w[i] = rnd.next(1, 100);
        }
        else if (type == 2) {
            // 类型2：随机数据，值范围 [1, 10^9]
            for(int i = 0; i < n; ++i) w[i] = rnd.next(1, 1000000000);
        }
        else if (type == 3) {
            // 类型3：大值数据，值范围 [10^8, 10^9]
            for(int i = 0; i < n; ++i) w[i] = rnd.next(100000000, 1000000000);
        }
        else if (type == 4) {
            // 类型4：边界数据
            int subtype = rnd.next(1, 3);
            if (subtype == 1) {
                // 全相同值
                int val = rnd.next(1, 100);
                fill(w.begin(), w.end(), val);
            } else if (subtype == 2) {
                // 严格递增序列
                int start = rnd.next(1, 100);
                for(int i = 0; i < n; ++i) w[i] = start + i;
            } else {
                // 波浪形数据（大小交替）
                for(int i = 0; i < n; ++i) 
                    w[i] = (i % 2 == 0) ? rnd.next(100, 200) : rnd.next(1, 10);
            }
        }
        else if (type == 5) {
            // 类型5：反hack数据（首元素大，后续小）
            w[0] = rnd.next(10000, 50000);
            for(int i = 1; i < n; ++i) w[i] = rnd.next(1, 100);
        }
        else {
            // 默认：随机数据
            for(int i = 0; i < n; ++i) w[i] = rnd.next(1, 1000000000);
        }
        
        println(n);
        println(w);
    }
    
    return 0;
}
