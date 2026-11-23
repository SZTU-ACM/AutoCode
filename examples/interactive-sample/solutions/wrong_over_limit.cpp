// Deliberately buggy wrong solution: linear scan exceeds 20 queries when S > 20.
#include <iostream>

int main() {
    for (int cand = 1; cand <= 100; ++cand) {
        std::cout << "? " << cand << std::endl;
        int resp = 0;
        if (!(std::cin >> resp)) {
            return 0;
        }
        if (resp == 0) {
            std::cout << "! " << cand << std::endl;
            return 0;
        }
    }
    return 0;
}
