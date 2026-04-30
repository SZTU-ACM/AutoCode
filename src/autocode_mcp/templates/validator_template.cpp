// Validator 模板 - 基于 testlib.h
// 用于验证输入数据是否符合题目约束

#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    // 读取输入并验证约束
    // 示例：读取一个整数 N，范围 [1, 100000]
    // int n = inf.readInt(1, 100000, "n");
    // inf.readEoln();

    // 读取数组
    // for (int i = 0; i < n; i++) {
    //     inf.readInt(1, 1000000000, "a_i");
    //     if (i < n - 1) inf.readSpace();
    // }
    // inf.readEoln();

    // validator 结束前必须 readEof()，否则 testlib finalize guard 会报错。
    // 若需要容忍文件尾部额外空白，可先执行 inf.seekEof(); 再执行 inf.readEof();
    inf.readEof();
    return 0;
}
