#include <limits>
#include "testlib.h"

namespace {
bool sum_equals_safe(long long x, long long y, long long target) {
    if (y > 0 && x > std::numeric_limits<long long>::max() - y) {
        return false;
    }
    if (y < 0 && x < std::numeric_limits<long long>::min() - y) {
        return false;
    }
    return (x + y) == target;
}
}  // namespace

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    long long n = inf.readLong(1LL, 1000000000LL, "n");
    long long x = ouf.readLong();
    long long y = ouf.readLong();
    if (!ouf.seekEof()) {
        quitf(_pe, "Unexpected extra output tokens");
    }

    if (!sum_equals_safe(x, y, n)) {
        quitf(_wa, "Expected x + y == %lld, got %lld + %lld", n, x, y);
    }

    quitf(_ok, "Valid pair (%lld, %lld) sums to %lld", x, y, n);
}
