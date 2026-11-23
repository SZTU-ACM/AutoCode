// Time Complexity: O(log N) queries (<= 7 queries for N = 100)
#include <iostream>

int main() {
    int lo = 1;
    int hi = 100;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        std::cout << "? " << mid << std::endl;
        int resp = 0;
        if (!(std::cin >> resp)) {
            return 0;
        }
        if (resp == 0) {
            std::cout << "! " << mid << std::endl;
            return 0;
        }
        if (resp < 0) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return 0;
}
