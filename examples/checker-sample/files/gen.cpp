#include <algorithm>
#include <cstdlib>
#include <iostream>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int seed = argc > 1 ? std::atoi(argv[1]) : 1;
    int type = argc > 2 ? std::atoi(argv[2]) : 2;
    long long n_min = argc > 3 ? std::atoll(argv[3]) : 1LL;
    long long n_max = argc > 4 ? std::atoll(argv[4]) : 1000000000LL;

    n_min = std::max(1LL, std::min(n_min, 1000000000LL));
    n_max = std::max(n_min, std::min(n_max, 1000000000LL));
    rnd.setSeed(seed);

    long long n = 1;
    switch (type) {
        case 1:
            n = rnd.next(1LL, std::min(20LL, n_max));
            break;
        case 2:
            n = rnd.next(n_min, n_max);
            break;
        case 3:
            n = (seed % 2 == 0) ? n_max : 1LL;
            break;
        case 4:
            n = std::max(1LL, n_max - (seed % 100));
            break;
        default:
            n = rnd.next(n_min, n_max);
            break;
    }

    std::cout << n << "\n";
    return 0;
}
