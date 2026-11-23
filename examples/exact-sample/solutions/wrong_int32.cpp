// Deliberately buggy wrong solution: 32-bit int overflow on large elements.
#include <iostream>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int n = 0;
    if (!(std::cin >> n)) {
        return 0;
    }
    int sum = 0;
    for (int i = 0; i < n; ++i) {
        int x = 0;
        std::cin >> x;
        sum += x;
    }
    std::cout << sum << "\n";
    return 0;
}
