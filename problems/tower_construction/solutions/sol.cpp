#include <bits/stdc++.h>
using namespace std;

void solve() {
    int n;
    cin >> n;
    
    // 使用前缀和数组
    vector<long long> s(n + 1, 0);
    for (int i = 1; i <= n; ++i) {
        long long w;
        cin >> w;
        s[i] = s[i - 1] + w;
    }

    vector<int> dp(n + 1, 0);
    vector<long long> val(n + 1, 0);
    
    // 小根堆，存储 pair<所需要的下一个S的最小值, 对应的下标j>
    priority_queue<pair<long long, int>, vector<pair<long long, int>>, greater<>> pq;
    
    // 初始化，0 个积木构成 0 座塔，总和为 0，最后一块塔的宽度也为 0
    pq.emplace(0LL, 0);
    
    int best_dp = 0;
    int best_j = 0;
    
    for (int i = 1; i <= n; ++i) {
        // 将所有对于当前 s[i] 已经合法的 j 弹出来，并更新当前"可用的最优状态"
        while (!pq.empty() && pq.top().first < s[i]) {
            auto [req_sum, j] = pq.top();
            pq.pop();
            
            // 贪心优先级：1. dp 更大；2. dp 相同下 j 更大（使 val[i] 更小）
            if (dp[j] > best_dp) {
                best_dp = dp[j];
                best_j = j;
            } else if (dp[j] == best_dp && j > best_j) {
                best_j = j;
            }
        }
        
        // 基于可用的最优状态转移
        dp[i] = best_dp + 1;
        val[i] = s[i] - s[best_j];
        
        // 将当前状态作为未来的跳板放入堆中
        pq.emplace(s[i] + val[i], i);
    }
    
    // 最终答案即为前 n 块积木最多能构建的塔数
    cout << dp[n] << '\n';
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int T = 1;
    cin >> T;
    while (T--) {
        solve();
    }
    return 0;
}