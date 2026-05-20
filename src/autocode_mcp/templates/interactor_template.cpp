// Interactor 模板 - 基于 testlib.h
// registerInteraction(argc, argv) 调用约定：
//   interactor <input-file> <output-file> [answer-file]
// 使用 inf 读取测试输入，使用 tout 向选手程序输出，使用 ouf 读取选手输出。
// 不要用 std::cout 向选手输出；每次写入 tout 后必须 flush。

#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    // 读取输入数据。交互题通常把隐藏参数、随机种子或测试配置放在 input-file 中。
    // int n = inf.readInt();

    // 向选手发送数据，并立即 flush。使用 endl 也会 flush，但显式 flush 更清楚。
    // tout << n << '\n';
    // tout.flush();

    // 读取选手输出。ouf 的格式/范围错误会按 testlib 语义给 WA/PE。
    // int answer = ouf.readInt();

    // 验证答案；协议错误、查询次数超限、非法最终答案都应 quitf(_wa, ...)。
    // if (answer == expected) {
    //     quitf(_ok, "Correct");
    // } else {
    //     quitf(_wa, "Wrong answer");
    // }

    quitf(_ok, "Interactor template");
}
