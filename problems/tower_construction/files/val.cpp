// 积木高塔 - 数据验证器
// 使用 testlib.h 库
// 验证输入数据是否符合题目约束

#include "testlib.h"
#include <iostream>

using namespace std;

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    int t = inf.readInt(1, 10000, "t");
    inf.readEoln();

    long long sum_n = 0;

    for (int i = 0; i < t; ++i) {
        int n = inf.readInt(1, 200000, "n");
        inf.readEoln();
        
        sum_n += n;

        for (int j = 0; j < n; ++j) {
            inf.readInt(1, 1000000000, "w_i");
            if (j < n - 1)
                inf.readSpace();
        }
        inf.readEoln();
    }

    inf.readEof();
    
    // 验证 N 总和不超过 200,000
    ensuref(sum_n <= 200000, "N 的总和超过 200,000");

    return 0;
}
