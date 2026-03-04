#include <bits/stdc++.h>
using namespace std;

void solve() {
    int n;
    cin >> n;
    vector<int> a(n);
    for (int i = 0; i < n; ++i) {
        cin >> a[i];
        if (a[i] == 0) a[i] = -1;
    }
    vector<vector<int>> adj(n);
    for (int i = 0; i < n - 1; ++i) {
        int u, v;
        cin >> u >> v;
        u--, v--;
        adj[u].emplace_back(v);
        adj[v].emplace_back(u);
    }
    vector<int> dp(n);
    [&](this auto&& self, int u, int p) -> int {
        long long cur = a[u];
        for (int v : adj[u]) {
            if (v != p) {
                cur += max(0, self(v, u));
            }
        }
        return dp[u] = cur;
    }(0, -1);
    vector<int> ans(n);
    ans[0] = dp[0];
    [&](this auto&& self, int u, int v) -> void {
        if (v) ans[v] = dp[v] + max(0, ans[u] - max(0, dp[v]));
        for (int c : adj[v]) {
            if (c != u) {
                self(v, c);
            }
        }
    }(-1, 0);
    for (int x : ans) cout << x << ' ';
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int T = 1;
    // cin >> T;
    while (T--) {
        solve();
    }
    return 0;
}
