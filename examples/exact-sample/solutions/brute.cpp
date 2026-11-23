// Time Complexity: O(n)
#include <cstdint>
#include <iostream>
#include <numeric>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int n = 0;
    if (!(std::cin >> n)) {
        return 0;
    }
    std::vector<std::int64_t> a(n);
    for (int i = 0; i < n; ++i) {
        std::cin >> a[i];
    }
    std::int64_t total = std::accumulate(a.begin(), a.end(), std::int64_t{0});
    std::cout << total << "\n";
    return 0;
}
