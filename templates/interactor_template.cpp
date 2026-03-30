// Interactor 模板 - 基于 testlib.h
// 用于交互题，与选手程序进行交互

#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    // 读取输入数据
    // int n = inf.readInt();

    // 与选手交互
    // std::cout << n << std::endl;
    // std::cout.flush();

    // 读取选手输出
    // int answer = ouf.readInt();

    // 验证答案
    // if (answer == expected) {
    //     quitf(_ok, "Correct");
    // } else {
    //     quitf(_wa, "Wrong answer");
    // }

    quitf(_ok, "Interactor template");
}
