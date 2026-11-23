#include <algorithm>
#include <cstdlib>
#include <iostream>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int seed = argc > 1 ? std::atoi(argv[1]) : 1;
    int type = argc > 2 ? std::atoi(argv[2]) : 2;
    int n_min = argc > 3 ? std::atoi(argv[3]) : 1;
    int n_max = argc > 4 ? std::atoi(argv[4]) : 100;

    n_min = std::max(1, std::min(n_min, 100));
    n_max = std::max(n_min, std::min(n_max, 100));
    rnd.setSeed(seed);

    int secret = 1;
    switch (type) {
        case 1:
            secret = rnd.next(1, std::min(10, n_max));
            break;
        case 2:
            secret = rnd.next(n_min, n_max);
            break;
        case 3:
            secret = (seed % 3 == 0) ? 1 : n_max;
            break;
        case 4:
            secret = rnd.next(std::max(80, n_min), n_max);
            break;
        default:
            secret = rnd.next(n_min, n_max);
            break;
    }

    std::cout << secret << "\n";
    return 0;
}
