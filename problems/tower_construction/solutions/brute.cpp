#include <iostream>
#include <vector>
#include <algorithm>

using namespace std;

// Recursive Brute Force
int max_towers_rec(const vector<long long>& w, int idx, long long last_sum) {
    if (idx == w.size()) return 0;
    
    long long current_sum = 0;
    int best = -1000000;
    
    for (int i = idx; i < w.size(); ++i) {
        current_sum += w[i];
        if (current_sum > last_sum) {
            if (i == w.size() - 1) {
                best = max(best, 1);
            } else {
                int res = max_towers_rec(w, i + 1, current_sum);
                if (res > 0) {
                    best = max(best, 1 + res);
                }
            }
        }
    }
    return best;
}

void solve() {
    int n;
    if (!(cin >> n)) return;
    vector<long long> w(n);
    for (int i = 0; i < n; ++i) cin >> w[i];
    
    int ans = max_towers_rec(w, 0, 0);
    // If failed, ans < 0. But valid input guaranteed >= 1 solution.
    if (ans < 0) ans = 0; 
    cout << ans << endl;
}

int main() {
    int t;
    if (cin >> t) {
        while (t--) {
            solve();
        }
    }
    return 0;
}
