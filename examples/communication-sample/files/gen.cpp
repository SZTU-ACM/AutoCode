#include <cstdlib>
#include <iostream>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int seed = argc > 1 ? std::atoi(argv[1]) : 1;
    int type = argc > 2 ? std::atoi(argv[2]) : 2;
    rnd.setSeed(seed);

    int mode = 0;
    int x = 0;
    switch (type) {
        case 1:
            mode = 0;
            x = rnd.next(0, 5);
            break;
        case 2:
            mode = 0;
            x = rnd.next(0, 15);
            break;
        case 3:
            mode = 1;
            x = (seed % 2 == 0) ? 15 : 0;
            break;
        case 4:
            mode = 1;
            x = rnd.next(8, 15);
            break;
        default:
            mode = rnd.next(0, 1);
            x = rnd.next(0, 15);
            break;
    }

    std::cout << mode << " " << x << "\n";
    return 0;
}
