// Generator 模板 - 基于 testlib.h
// 用于生成测试数据
//
// 安全提示：
// 1. 使用 do-while 循环生成不重复元素时，务必添加 attempts 计数器防止死循环
// 2. 确保参数范围足够大以生成所需数量的不重复元素
// 3. 示例：生成不重复坐标时，确保 L >= N + 1

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
        case 4:  // tle（建议做结构性卡法，而非仅拉满参数）
            n = n_max;
            break;
        default:
            n = rnd.next(n_min, n_max);
    }

    // 输出测试数据
    std::cout << n << std::endl;

    // 示例：生成不重复元素的数组（带安全检查）
    // 确保 n 不超过值域范围
    // int value_range = 1000000000;
    // if (n > value_range) n = value_range;  // 防止无法生成不重复元素

    for (int i = 0; i < n; i++) {
        if (i > 0) std::cout << " ";
        if (type == 4) {
            // 示例：构造大量重复值，常用于诱导低效去重/统计逻辑。
            std::cout << (i % 2 == 0 ? 1 : 1000000000);
        } else {
            std::cout << rnd.next(1, 1000000000);
        }
    }
    std::cout << std::endl;

    return 0;
}

// === 生成不重复坐标的安全示例 ===
// 当需要生成 N 个不重复的坐标 (x, y) 时：
//
// void generateUniqueCoords(int n, long long L) {
//     // 安全检查：确保 L 足够大
//     L = std::max(L, (long long)(n + 1));
//
//     std::set<std::pair<long long, long long>> used;
//     int attempts = 0;
//     const int MAX_ATTEMPTS = n * 100;  // 防止死循环
//
//     while (used.size() < n && attempts < MAX_ATTEMPTS) {
//         long long x = rnd.next(0LL, L - 1);
//         long long y = rnd.next(0LL, L - 1);
//         auto coord = std::make_pair(x, y);
//
//         if (used.find(coord) == used.end()) {
//             used.insert(coord);
//             std::cout << x << " " << y << std::endl;
//         }
//         attempts++;
//     }
//
//     // 如果无法生成足够的不重复坐标，输出警告或调整参数
//     if (used.size() < n) {
//         // 可以选择输出剩余坐标或抛出错误
//     }
// }
