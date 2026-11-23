// Time Complexity: O(1)
#include <iostream>

int main() {
    long long n = 0;
    if (!(std::cin >> n)) {
        return 0;
    }
    long long x = (n > 1) ? (n / 2) : 1;
    long long y = n - x;
    std::cout << x << " " << y << "\n";
    return 0;
}
