// Deliberately buggy wrong solution: outputs (1, n) so x + y = n + 1 != n.
#include <iostream>

int main() {
    long long n = 0;
    if (!(std::cin >> n)) {
        return 0;
    }
    std::cout << 1 << " " << n << "\n";
    return 0;
}
