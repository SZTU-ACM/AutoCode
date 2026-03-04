#include "testlib.h"
#include <bits/stdc++.h>

using namespace std;

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int n = atoi(argv[1]);
    int t = atoi(argv[2]); // type 
    // t=0: random tree
    // t=1: star graph
    // t=2: line graph
    // t=3: pure max/min (all 1 / all 0)
    int offset = atoi(argv[3]);

    println(n);

    vector<int> a(n);
    if (t == 3) {
        int v = rnd.next(0, 1);
        for (int i = 0; i < n; ++i) a[i] = v;
    } else {
        for (int i = 0; i < n; ++i) a[i] = rnd.next(0, 1);
    }
    println(a);

    vector<int> p(n);
    for(int i = 0; i < n; i++) p[i] = i;
    shuffle(p.begin() + 1, p.end());

    vector<pair<int, int>> edges;
    for (int i = 1; i < n; i++) {
        int u;
        if (t == 0 || t == 3) {
            u = p[rnd.next(0, i - 1)];
        } else if (t == 1) {
            u = p[0];
        } else if (t == 2) {
            u = p[i - 1];
        }
        int v = p[i];
        edges.push_back({u + 1, v + 1});
    }
    
    shuffle(edges.begin(), edges.end());
    for(auto& e: edges) {
        if(rnd.next(2)) swap(e.first, e.second);
    }

    for (int i = 0; i < edges.size(); i++) {
        println(edges[i].first, edges[i].second);
    }

    return 0;
}
