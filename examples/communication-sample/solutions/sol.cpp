// Time complexity: O(1)
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
        encoded.reserve(12);
        for (int b = 3; b >= 0; --b) {
            char bit = ((x >> b) & 1) ? '1' : '0';
            encoded.append(3, bit);
        }
        std::cout << encoded << "\n";
    } else {
        std::string noisy;
        std::cin >> noisy;
        int x = 0;
        for (int i = 0; i < 4; ++i) {
            int ones = 0;
            for (int j = 0; j < 3; ++j) {
                if (i * 3 + j < static_cast<int>(noisy.size()) && noisy[i * 3 + j] == '1') {
                    ++ones;
                }
            }
            x = (x << 1) | (ones >= 2 ? 1 : 0);
        }
        std::cout << x << "\n";
    }
    return 0;
}
