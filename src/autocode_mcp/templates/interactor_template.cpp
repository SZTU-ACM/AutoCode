// Interactor 模板 - 基于 testlib.h
// registerInteraction(argc, argv) 调用约定：
//   interactor <input-file> <output-file> [answer-file]
// - inf: 读取测试输入（argv[1]），通常存放隐藏参数、随机种子或测试配置
// - std::cout: 向选手程序发送数据，每次发送后必须 flush（如 std::endl 或 std::cout.flush()）
// - ouf: 读取选手输出（来自 stdin 管道）；格式/范围/EOF 错误会按 testlib 语义判 WA/PE
// - tout: 可选地向 argv[2] 输出文件写入交互摘要或给 checker 使用的记录（不会发给选手！）
// - ans: 可选地读取参考答案文件（argv[3]）

#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    // 读取隐藏测试输入
    // int n = inf.readInt();

    // 向选手发送提示并立即 flush
    // std::cout << n << '\n';
    // std::cout.flush();

    // 读取选手输出
    // int answer = ouf.readInt();

    // 可选：记录摘要到 tout（argv[2]）
    // tout << answer << '\n';

    // 判定结果：协议错误、查询超限、越界、错误答案均应 quitf(_wa, ...)
    // if (answer == expected) {
    //     quitf(_ok, "Correct");
    // } else {
    //     quitf(_wa, "Wrong answer");
    // }

    quitf(_ok, "Interactor template");
}
