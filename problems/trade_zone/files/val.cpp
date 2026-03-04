#include "testlib.h"
#include <vector>

using namespace std;

const int MAX_N = 200000;

struct DSU {
    vector<int> p;
    DSU(int n) {
        p.resize(n + 1);
        for (int i = 1; i <= n; i++) p[i] = i;
    }
    int find(int x) {
        return p[x] == x ? x : p[x] = find(p[x]);
    }
    bool unite(int x, int y) {
        int rx = find(x), ry = find(y);
        if (rx == ry) return false;
        p[rx] = ry;
        return true;
    }
};

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    int n = inf.readInt(2, MAX_N, "n");
    inf.readEoln();

    for (int i = 0; i < n; i++) {
        inf.readInt(0, 1, "a_i");
        if (i < n - 1) inf.readSpace();
    }
    inf.readEoln();

    DSU dsu(n);
    for (int i = 0; i < n - 1; i++) {
        int u = inf.readInt(1, n, "u_i");
        inf.readSpace();
        int v = inf.readInt(1, n, "v_i");
        inf.readEoln();

        ensuref(u != v, "Edges must connect different vertices");
        ensuref(dsu.unite(u, v), "Graph must be a tree (no cycles)");
    }
    inf.readEof();

    return 0;
}
