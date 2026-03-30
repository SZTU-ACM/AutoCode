// Generator 模板 - 基于 testlib.h
// 用于生成测试数据

#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    // 参数解析
    // gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>
    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    int n_min = atoi(argv[3]);
    int n_max = atoi(argv[4]);
    int t_min = atoi(argv[5]);
    int t_max = atoi(argv[6]);

    rnd.setSeed(seed);

    // 根据类型生成数据
    // type: 1=tiny, 2=random, 3=extreme, 4=tle
    int n;
    switch (type) {
        case 1:  // tiny
            n = rnd.next(1, 10);
            break;
        case 2:  // random
            n = rnd.next(n_min, n_max);
            break;
        case 3:  // extreme
            n = n_max;
            break;
        case 4:  // tle
            n = n_max;
            break;
        default:
            n = rnd.next(n_min, n_max);
    }

    // 输出测试数据
    std::cout << n << std::endl;

    for (int i = 0; i < n; i++) {
        if (i > 0) std::cout << " ";
        std::cout << rnd.next(1, 1000000000);
    }
    std::cout << std::endl;

    return 0;
}
