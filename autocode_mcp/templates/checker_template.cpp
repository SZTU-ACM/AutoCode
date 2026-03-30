// Checker 模板 - 基于 testlib.h
// 用于比较选手输出和标准答案

#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    // 读取输入
    int n = inf.readInt();

    // 读取答案
    long long jury = ans.readLong();

    // 读取选手输出
    long long contestant = ouf.readLong();

    // 比较
    if (jury == contestant) {
        quitf(_ok, "Correct");
    } else {
        quitf(_wa, "Expected %lld, got %lld", jury, contestant);
    }
}
