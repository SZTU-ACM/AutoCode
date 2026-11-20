/**
 * AutoCode Grader-Linked Communication Template (IOI / QOJ style)
 *
 * Compiled and linked together with contestant source solutions/sol.cpp:
 *   g++ -std=c++20 -O2 -I files solutions/sol.cpp files/grader.cpp -o solutions/sol
 *
 * Exit code contract (aligned with testlib.h):
 *   0 = AC ("ok bits_used=... bytes_used=...")
 *   1 = WA ("wrong answer ...")
 *   2 = PE ("wrong output format ...")
 *   7 = PC ("points <score> ...")
 */

#include <iostream>
#include <vector>
#include "problem.h"

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int n;
    if (!(std::cin >> n)) {
        std::cerr << "FAIL cannot read input\n";
        return 3;
    }
    std::vector<int> a(n);
    for (int i = 0; i < n; ++i) {
        std::cin >> a[i];
    }

    std::vector<int> encoded = encode(n, a);
    for (int bit : encoded) {
        if (bit != 0 && bit != 1) {
            std::cerr << "wrong output format: encode must return bits in {0, 1}\n";
            return 2;
        }
    }

    int bits_used = static_cast<int>(encoded.size());
    int bytes_used = (bits_used + 7) / 8;
    std::vector<int> decoded = decode(n, encoded);

    if (decoded != a) {
        std::cerr << "wrong answer: decoded sequence does not match original\n";
        return 1;
    }

    std::cerr << "ok bits_used=" << bits_used << " bytes_used=" << bytes_used << " rounds=2\n";
    return 0;
}
