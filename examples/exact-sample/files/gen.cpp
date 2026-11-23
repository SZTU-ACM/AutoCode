#include <algorithm>
#include <cstdlib>
#include <iostream>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int seed = argc > 1 ? std::atoi(argv[1]) : 1;
    int type = argc > 2 ? std::atoi(argv[2]) : 2;
    int n_min = argc > 3 ? std::atoi(argv[3]) : 1;
    int n_max = argc > 4 ? std::atoi(argv[4]) : 200000;

    n_min = std::max(1, std::min(n_min, 200000));
    n_max = std::max(n_min, std::min(n_max, 200000));
    rnd.setSeed(seed);

    int n = 1;
    switch (type) {
        case 1:
            n = rnd.next(1, std::min(10, n_max));
            break;
        case 2:
            n = rnd.next(n_min, n_max);
            break;
        case 3:
            n = std::max(10, n_max);
            break;
        case 4:
            n = n_max;
            break;
        default:
            n = rnd.next(n_min, n_max);
            break;
    }

    std::cout << n << "\n";
    for (int i = 0; i < n; ++i) {
        if (i > 0) {
            std::cout << " ";
        }
        int val = 0;
        switch (type) {
            case 1:
                val = rnd.next(-100, 100);
                break;
            case 2:
                val = rnd.next(-1000000000, 1000000000);
                break;
            case 3:
                val = 1000000000 - (i % 7);
                break;
            case 4:
                val = (i % 3 == 0) ? -1000000000 : 1000000000;
                break;
            default:
                val = rnd.next(-1000000000, 1000000000);
                break;
        }
        std::cout << val;
    }
    std::cout << "\n";
    return 0;
}
