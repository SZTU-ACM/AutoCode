// Independent oracle: step-10 block search (at most 19 queries <= 20)
#include <iostream>

int main() {
    int low = 1;
    for (int probe = 10; probe <= 100; probe += 10) {
        std::cout << "? " << probe << std::endl;
        int resp = 0;
        if (!(std::cin >> resp)) {
            return 0;
        }
        if (resp == 0) {
            std::cout << "! " << probe << std::endl;
            return 0;
        }
        if (resp > 0) {
            for (int cand = low; cand < probe; ++cand) {
                std::cout << "? " << cand << std::endl;
                int r2 = 0;
                if (!(std::cin >> r2)) {
                    return 0;
                }
                if (r2 == 0) {
                    std::cout << "! " << cand << std::endl;
                    return 0;
                }
            }
            return 0;
        }
        low = probe + 1;
    }
    return 0;
}
