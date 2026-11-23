// Deliberately buggy wrong solution: sends 4 raw bits without error correction (fails Subtask 2).
#include <iostream>
#include <string>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::string phase;
    if (!(std::cin >> phase)) {
        return 0;
    }

    if (phase == "first") {
        int mode = 0;
        int x = 0;
        std::cin >> mode >> x;
        std::string encoded;
        encoded.reserve(4);
        for (int b = 3; b >= 0; --b) {
            encoded.push_back(((x >> b) & 1) ? '1' : '0');
        }
        std::cout << encoded << "\n";
    } else {
        std::string noisy;
        std::cin >> noisy;
        int x = 0;
        for (char c : noisy) {
            x = (x << 1) | (c == '1' ? 1 : 0);
        }
        std::cout << x << "\n";
    }
    return 0;
}
