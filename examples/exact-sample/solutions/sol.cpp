// Time Complexity: O(n)
#include <iostream>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int n = 0;
    if (!(std::cin >> n)) {
        return 0;
    }
    long long sum = 0;
    for (int i = 0; i < n; ++i) {
        long long x = 0;
        std::cin >> x;
        sum += x;
    }
    std::cout << sum << "\n";
    return 0;
}
