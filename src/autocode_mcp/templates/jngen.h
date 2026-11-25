/*
 * AutoCode jngen.h - Self-contained C++17 deterministic generator & anti-hash header.
 *
 * Designed to compose cleanly alongside #include "testlib.h" (or standalone)
 * under C++17 / C++20 on GCC, Clang (including Apple Clang), and MinGW without
 * <bits/stdc++.h> or deprecated C rand()/random() calls.
 */

#ifndef _AUTOCODE_JNGEN_H_
#define _AUTOCODE_JNGEN_H_

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <limits>
#include <numeric>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace jngen {

// ---------------------------------------------------------------------------
// Deterministic SplitMix64 & PRNG
// ---------------------------------------------------------------------------

inline std::uint64_t splitmix64(std::uint64_t x) noexcept {
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

class SplitMix64 {
public:
    explicit SplitMix64(std::uint64_t seed = 0x9e3779b97f4a7c15ULL) noexcept
        : state_(seed) {}

    void set_seed(std::uint64_t seed) noexcept { state_ = seed; }

    std::uint64_t next_u64() noexcept {
        state_ += 0x9e3779b97f4a7c15ULL;
        std::uint64_t z = state_;
        z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
        z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
        return z ^ (z >> 31);
    }

    std::uint64_t state() const noexcept { return state_; }

private:
    std::uint64_t state_;
};

class Random {
public:
    explicit Random(std::uint64_t seed = 1ULL) noexcept : gen_(seed) {}

    void set_seed(std::uint64_t seed) noexcept { gen_.set_seed(seed); }

    std::uint64_t next_u64() noexcept { return gen_.next_u64(); }

    // Uniform integer in [0, bound - 1] using rejection sampling (no modulo bias).
    std::uint64_t next_bound(std::uint64_t bound) {
        if (bound == 0ULL) {
            throw std::invalid_argument("jngen::Random::next_bound requires bound > 0");
        }
        std::uint64_t threshold = (-bound) % bound;
        while (true) {
            std::uint64_t r = gen_.next_u64();
            if (r >= threshold) {
                return r % bound;
            }
        }
    }

    int next_int(int lo, int hi) {
        return static_cast<int>(next_ll(static_cast<long long>(lo), static_cast<long long>(hi)));
    }

    long long next_ll(long long lo, long long hi) {
        if (lo > hi) {
            throw std::invalid_argument("jngen::Random::next_ll requires lo <= hi");
        }
        std::uint64_t span =
            static_cast<std::uint64_t>(hi) - static_cast<std::uint64_t>(lo);
        if (span == std::numeric_limits<std::uint64_t>::max()) {
            return static_cast<long long>(gen_.next_u64());
        }
        std::uint64_t offset = next_bound(span + 1ULL);
        return static_cast<long long>(static_cast<std::uint64_t>(lo) + offset);
    }

    // Weighted next in [lo, hi] mirroring testlib wnext semantics.
    long long wnext_ll(long long lo, long long hi, int weight) {
        if (lo > hi) {
            throw std::invalid_argument("jngen::Random::wnext_ll requires lo <= hi");
        }
        long long res = next_ll(lo, hi);
        for (int i = 0; i < weight; ++i) {
            long long cand = next_ll(lo, hi);
            if (cand > res) res = cand;
        }
        for (int i = 0; i < -weight; ++i) {
            long long cand = next_ll(lo, hi);
            if (cand < res) res = cand;
        }
        return res;
    }

    int wnext_int(int lo, int hi, int weight) {
        return static_cast<int>(wnext_ll(lo, hi, weight));
    }

    double next_double() noexcept {
        return static_cast<double>(gen_.next_u64() >> 11) * (1.0 / 9007199254740992.0);
    }

    template <typename RandomIt>
    void shuffle_range(RandomIt first, RandomIt last) {
        auto n = last - first;
        for (decltype(n) i = n - 1; i > 0; --i) {
            auto j = static_cast<decltype(n)>(next_bound(static_cast<std::uint64_t>(i + 1)));
            std::swap(first[i], first[j]);
        }
    }

    template <typename Container>
    void shuffle_vec(Container& c) {
        if (!c.empty()) {
            shuffle_range(c.begin(), c.end());
        }
    }

    template <typename T>
    const T& choice(const std::vector<T>& items) {
        if (items.empty()) {
            throw std::invalid_argument("jngen::Random::choice on empty vector");
        }
        return items[static_cast<std::size_t>(next_bound(items.size()))];
    }

private:
    SplitMix64 gen_;
};

inline Random& default_rng() noexcept {
    static Random instance(0x243f6a8885a308d3ULL);
    return instance;
}

inline void set_seed(std::uint64_t seed) noexcept {
    default_rng().set_seed(seed);
}

// ---------------------------------------------------------------------------
// Sequences, Permutations, and Partitions
// ---------------------------------------------------------------------------

inline std::vector<int> permutation(int n, int first = 1, Random& rng = default_rng()) {
    if (n < 0) {
        throw std::invalid_argument("jngen::permutation requires n >= 0");
    }
    std::vector<int> perm(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) {
        perm[static_cast<std::size_t>(i)] = first + i;
    }
    rng.shuffle_vec(perm);
    return perm;
}

inline std::vector<int> derangement(int n, int first = 1, Random& rng = default_rng()) {
    if (n < 0 || n == 1) {
        throw std::invalid_argument("jngen::derangement requires n == 0 or n >= 2");
    }
    if (n == 0) {
        return {};
    }
    std::vector<int> p = permutation(n, first, rng);
    for (int i = 0; i < n; ++i) {
        if (p[static_cast<std::size_t>(i)] == first + i) {
            int swap_idx = (i + 1 < n) ? (i + 1) : 0;
            std::swap(p[static_cast<std::size_t>(i)], p[static_cast<std::size_t>(swap_idx)]);
        }
    }
    return p;
}

inline std::vector<long long> random_array(
    int n,
    long long lo,
    long long hi,
    Random& rng = default_rng()
) {
    if (n < 0 || lo > hi) {
        throw std::invalid_argument("jngen::random_array invalid n or [lo, hi]");
    }
    std::vector<long long> arr(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) {
        arr[static_cast<std::size_t>(i)] = rng.next_ll(lo, hi);
    }
    return arr;
}

// Robert Floyd's O(n) distinct integer sampling over [lo, hi] (never loops infinitely).
inline std::vector<long long> distinct_array(
    int n,
    long long lo,
    long long hi,
    Random& rng = default_rng()
) {
    if (n < 0 || lo > hi) {
        throw std::invalid_argument("jngen::distinct_array invalid n or [lo, hi]");
    }
    if (n == 0) {
        return {};
    }
    std::uint64_t span =
        static_cast<std::uint64_t>(hi) - static_cast<std::uint64_t>(lo);
    if (span < std::numeric_limits<std::uint64_t>::max() &&
        static_cast<std::uint64_t>(n) > span + 1ULL) {
        throw std::invalid_argument("jngen::distinct_array range smaller than n");
    }
    std::vector<long long> out;
    out.reserve(static_cast<std::size_t>(n));
    std::unordered_set<long long> used;
    used.reserve(static_cast<std::size_t>(n) * 2U + 1U);
    long long start = hi - static_cast<long long>(n - 1);
    for (int idx = 0; idx < n; ++idx) {
        long long j = start + static_cast<long long>(idx);
        long long t = rng.next_ll(lo, j);
        if (used.insert(t).second) {
            out.push_back(t);
        } else {
            used.insert(j);
            out.push_back(j);
        }
    }
    rng.shuffle_vec(out);
    return out;
}

inline std::vector<long long> partition_sum(
    int count,
    long long total,
    long long min_part = 1,
    Random& rng = default_rng()
) {
    if (count <= 0) {
        throw std::invalid_argument("jngen::partition_sum requires count > 0");
    }
    if (total < static_cast<long long>(count) * min_part) {
        throw std::invalid_argument("jngen::partition_sum total < count * min_part");
    }
    long long remaining = total - static_cast<long long>(count) * min_part;
    if (count == 1) {
        return {total};
    }
    std::vector<long long> cuts;
    if (remaining == 0) {
        return std::vector<long long>(static_cast<std::size_t>(count), min_part);
    }
    // Stars-and-bars via sorted uniform cuts in [0, remaining].
    cuts.reserve(static_cast<std::size_t>(count - 1));
    for (int i = 0; i < count - 1; ++i) {
        cuts.push_back(rng.next_ll(0LL, remaining));
    }
    std::sort(cuts.begin(), cuts.end());
    std::vector<long long> parts(static_cast<std::size_t>(count));
    long long prev = 0;
    for (int i = 0; i < count - 1; ++i) {
        parts[static_cast<std::size_t>(i)] =
            min_part + (cuts[static_cast<std::size_t>(i)] - prev);
        prev = cuts[static_cast<std::size_t>(i)];
    }
    parts[static_cast<std::size_t>(count - 1)] = min_part + (remaining - prev);
    rng.shuffle_vec(parts);
    return parts;
}

// ---------------------------------------------------------------------------
// Trees
// ---------------------------------------------------------------------------

struct Tree {
    int n = 0;
    bool one_indexed = true;
    std::vector<std::pair<int, int>> edges;

    Tree relabeled(Random& rng = default_rng()) const {
        if (n <= 0) return *this;
        int base = one_indexed ? 1 : 0;
        std::vector<int> perm = permutation(n, base, rng);
        Tree res;
        res.n = n;
        res.one_indexed = one_indexed;
        res.edges.reserve(edges.size());
        for (const auto& e : edges) {
            int u = perm[static_cast<std::size_t>(e.first - base)];
            int v = perm[static_cast<std::size_t>(e.second - base)];
            if (rng.next_bound(2ULL) == 1ULL) {
                std::swap(u, v);
            }
            res.edges.emplace_back(u, v);
        }
        rng.shuffle_vec(res.edges);
        return res;
    }

    void print(std::ostream& os = std::cout, bool print_n = true) const {
        if (print_n) {
            os << n << '\n';
        }
        for (const auto& e : edges) {
            os << e.first << ' ' << e.second << '\n';
        }
    }

    std::string to_string(bool print_n = true) const {
        std::ostringstream oss;
        print(oss, print_n);
        return oss.str();
    }
};

inline Tree random_tree(
    int n,
    int elongation = 0,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0) {
        throw std::invalid_argument("jngen::random_tree requires n > 0");
    }
    int base = one_indexed ? 1 : 0;
    Tree t;
    t.n = n;
    t.one_indexed = one_indexed;
    t.edges.reserve(static_cast<std::size_t>(std::max(0, n - 1)));
    for (int i = 1; i < n; ++i) {
        int parent = rng.wnext_int(0, i - 1, elongation);
        t.edges.emplace_back(parent + base, i + base);
    }
    return relabel ? t.relabeled(rng) : t;
}

inline Tree prufer_tree(
    int n,
    bool one_indexed = true,
    Random& rng = default_rng()
) {
    if (n <= 0) {
        throw std::invalid_argument("jngen::prufer_tree requires n > 0");
    }
    int base = one_indexed ? 1 : 0;
    Tree t;
    t.n = n;
    t.one_indexed = one_indexed;
    if (n == 1) {
        return t;
    }
    if (n == 2) {
        t.edges.emplace_back(base, base + 1);
        return t.relabeled(rng);
    }
    std::vector<int> code(static_cast<std::size_t>(n - 2));
    std::vector<int> deg(static_cast<std::size_t>(n), 1);
    for (int i = 0; i < n - 2; ++i) {
        int v = rng.next_int(0, n - 1);
        code[static_cast<std::size_t>(i)] = v;
        ++deg[static_cast<std::size_t>(v)];
    }
    int ptr = 0;
    while (deg[static_cast<std::size_t>(ptr)] != 1) {
        ++ptr;
    }
    int leaf = ptr;
    t.edges.reserve(static_cast<std::size_t>(n - 1));
    for (int v : code) {
        t.edges.emplace_back(leaf + base, v + base);
        if (--deg[static_cast<std::size_t>(v)] == 1 && v < ptr) {
            leaf = v;
        } else {
            ++ptr;
            while (ptr < n && deg[static_cast<std::size_t>(ptr)] != 1) {
                ++ptr;
            }
            leaf = ptr;
        }
    }
    t.edges.emplace_back(leaf + base, (n - 1) + base);
    rng.shuffle_vec(t.edges);
    return t;
}

inline Tree path_tree(
    int n,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0) {
        throw std::invalid_argument("jngen::path_tree requires n > 0");
    }
    int base = one_indexed ? 1 : 0;
    Tree t;
    t.n = n;
    t.one_indexed = one_indexed;
    for (int i = 1; i < n; ++i) {
        t.edges.emplace_back(i - 1 + base, i + base);
    }
    return relabel ? t.relabeled(rng) : t;
}

inline Tree star_tree(
    int n,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0) {
        throw std::invalid_argument("jngen::star_tree requires n > 0");
    }
    int base = one_indexed ? 1 : 0;
    Tree t;
    t.n = n;
    t.one_indexed = one_indexed;
    for (int i = 1; i < n; ++i) {
        t.edges.emplace_back(base, i + base);
    }
    return relabel ? t.relabeled(rng) : t;
}

inline Tree caterpillar_tree(
    int n,
    int spine_len,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0 || spine_len <= 0 || spine_len > n) {
        throw std::invalid_argument("jngen::caterpillar_tree invalid n or spine_len");
    }
    int base = one_indexed ? 1 : 0;
    Tree t;
    t.n = n;
    t.one_indexed = one_indexed;
    for (int i = 1; i < spine_len; ++i) {
        t.edges.emplace_back(i - 1 + base, i + base);
    }
    for (int i = spine_len; i < n; ++i) {
        int attach = rng.next_int(0, spine_len - 1);
        t.edges.emplace_back(attach + base, i + base);
    }
    return relabel ? t.relabeled(rng) : t;
}

inline Tree binary_tree(
    int n,
    bool balanced = true,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0) {
        throw std::invalid_argument("jngen::binary_tree requires n > 0");
    }
    int base = one_indexed ? 1 : 0;
    Tree t;
    t.n = n;
    t.one_indexed = one_indexed;
    if (balanced) {
        for (int i = 1; i < n; ++i) {
            t.edges.emplace_back((i - 1) / 2 + base, i + base);
        }
    } else {
        std::vector<int> child_cnt(static_cast<std::size_t>(n), 0);
        std::vector<int> avail = {0};
        for (int i = 1; i < n; ++i) {
            std::size_t pick_idx = static_cast<std::size_t>(rng.next_bound(avail.size()));
            int p = avail[pick_idx];
            t.edges.emplace_back(p + base, i + base);
            if (++child_cnt[static_cast<std::size_t>(p)] == 2) {
                avail[pick_idx] = avail.back();
                avail.pop_back();
            }
            avail.push_back(i);
        }
    }
    return relabel ? t.relabeled(rng) : t;
}

inline Tree broom_tree(
    int n,
    int handle_len,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0 || handle_len <= 0 || handle_len > n) {
        throw std::invalid_argument("jngen::broom_tree invalid n or handle_len");
    }
    int base = one_indexed ? 1 : 0;
    Tree t;
    t.n = n;
    t.one_indexed = one_indexed;
    for (int i = 1; i < handle_len; ++i) {
        t.edges.emplace_back(i - 1 + base, i + base);
    }
    for (int i = handle_len; i < n; ++i) {
        t.edges.emplace_back((handle_len - 1) + base, i + base);
    }
    return relabel ? t.relabeled(rng) : t;
}

// ---------------------------------------------------------------------------
// Graphs & DAGs
// ---------------------------------------------------------------------------

struct Graph {
    int n = 0;
    bool directed = false;
    bool one_indexed = true;
    std::vector<std::pair<int, int>> edges;

    Graph relabeled(Random& rng = default_rng()) const {
        if (n <= 0) return *this;
        int base = one_indexed ? 1 : 0;
        std::vector<int> perm = permutation(n, base, rng);
        Graph res;
        res.n = n;
        res.directed = directed;
        res.one_indexed = one_indexed;
        res.edges.reserve(edges.size());
        for (const auto& e : edges) {
            int u = perm[static_cast<std::size_t>(e.first - base)];
            int v = perm[static_cast<std::size_t>(e.second - base)];
            if (!directed && u != v && rng.next_bound(2ULL) == 1ULL) {
                std::swap(u, v);
            }
            res.edges.emplace_back(u, v);
        }
        rng.shuffle_vec(res.edges);
        return res;
    }

    void print(std::ostream& os = std::cout, bool print_header = true) const {
        if (print_header) {
            os << n << ' ' << edges.size() << '\n';
        }
        for (const auto& e : edges) {
            os << e.first << ' ' << e.second << '\n';
        }
    }

    std::string to_string(bool print_header = true) const {
        std::ostringstream oss;
        print(oss, print_header);
        return oss.str();
    }
};

namespace detail {

inline std::uint64_t pack_edge_key(int u, int v, bool directed) noexcept {
    if (!directed && u > v) {
        std::swap(u, v);
    }
    return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(u)) << 32) |
           static_cast<std::uint64_t>(static_cast<std::uint32_t>(v));
}

}  // namespace detail

inline Graph random_graph(
    int n,
    long long m,
    bool directed = false,
    bool connected = true,
    bool allow_self_loops = false,
    bool allow_multi_edges = false,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0 || m < 0) {
        throw std::invalid_argument("jngen::random_graph invalid n or m");
    }
    if (connected && m < static_cast<long long>(n - 1)) {
        throw std::invalid_argument("jngen::random_graph connected graph requires m >= n - 1");
    }
    long long max_edges = std::numeric_limits<long long>::max();
    if (!allow_multi_edges) {
        long long nn = static_cast<long long>(n);
        if (directed) {
            max_edges = allow_self_loops ? (nn * nn) : (nn * (nn - 1));
        } else {
            max_edges = allow_self_loops ? (nn * (nn + 1) / 2) : (nn * (nn - 1) / 2);
        }
        if (m > max_edges) {
            throw std::invalid_argument("jngen::random_graph m exceeds simple graph capacity");
        }
    }

    int base = one_indexed ? 1 : 0;
    Graph g;
    g.n = n;
    g.directed = directed;
    g.one_indexed = one_indexed;
    g.edges.reserve(static_cast<std::size_t>(m));
    std::unordered_set<std::uint64_t> seen;
    if (!allow_multi_edges) {
        seen.reserve(static_cast<std::size_t>(m) * 2U + 1U);
    }

    if (connected && n > 1) {
        Tree st = random_tree(n, 0, false, false, rng);
        for (const auto& e : st.edges) {
            int u = e.first;
            int v = e.second;
            if (directed && rng.next_bound(2ULL) == 1ULL) {
                std::swap(u, v);
            }
            if (!allow_multi_edges) {
                seen.insert(detail::pack_edge_key(u, v, directed));
            }
            g.edges.emplace_back(u + base, v + base);
        }
    }

    // If dense simple graph (> 50% capacity), enumerate remaining edges to guarantee O(max_edges).
    if (!allow_multi_edges && max_edges <= 2000000LL && m * 2LL > max_edges) {
        std::vector<std::pair<int, int>> pool;
        pool.reserve(static_cast<std::size_t>(max_edges - static_cast<long long>(g.edges.size())));
        for (int u = 0; u < n; ++u) {
            int v_start = directed ? 0 : u;
            for (int v = v_start; v < n; ++v) {
                if (!allow_self_loops && u == v) continue;
                if (seen.find(detail::pack_edge_key(u, v, directed)) == seen.end()) {
                    pool.emplace_back(u, v);
                }
            }
        }
        rng.shuffle_vec(pool);
        std::size_t need = static_cast<std::size_t>(m) - g.edges.size();
        for (std::size_t i = 0; i < need; ++i) {
            g.edges.emplace_back(pool[i].first + base, pool[i].second + base);
        }
    } else {
        while (static_cast<long long>(g.edges.size()) < m) {
            int u = rng.next_int(0, n - 1);
            int v = rng.next_int(0, n - 1);
            if (!allow_self_loops && u == v) continue;
            if (!allow_multi_edges) {
                if (!seen.insert(detail::pack_edge_key(u, v, directed)).second) {
                    continue;
                }
            }
            g.edges.emplace_back(u + base, v + base);
        }
    }

    return relabel ? g.relabeled(rng) : g;
}

inline Graph random_dag(
    int n,
    long long m,
    bool weakly_connected = false,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0 || m < 0) {
        throw std::invalid_argument("jngen::random_dag invalid n or m");
    }
    if (weakly_connected && m < static_cast<long long>(n - 1)) {
        throw std::invalid_argument("jngen::random_dag weakly_connected requires m >= n - 1");
    }
    long long nn = static_cast<long long>(n);
    long long max_edges = nn * (nn - 1) / 2;
    if (m > max_edges) {
        throw std::invalid_argument("jngen::random_dag m exceeds DAG capacity n*(n-1)/2");
    }

    int base = one_indexed ? 1 : 0;
    Graph g;
    g.n = n;
    g.directed = true;
    g.one_indexed = one_indexed;
    g.edges.reserve(static_cast<std::size_t>(m));
    std::unordered_set<std::uint64_t> seen;
    seen.reserve(static_cast<std::size_t>(m) * 2U + 1U);

    if (weakly_connected && n > 1) {
        for (int v = 1; v < n; ++v) {
            int u = rng.next_int(0, v - 1);
            seen.insert(detail::pack_edge_key(u, v, true));
            g.edges.emplace_back(u + base, v + base);
        }
    }

    if (max_edges <= 2000000LL && m * 2LL > max_edges) {
        std::vector<std::pair<int, int>> pool;
        pool.reserve(static_cast<std::size_t>(max_edges - static_cast<long long>(g.edges.size())));
        for (int u = 0; u < n; ++u) {
            for (int v = u + 1; v < n; ++v) {
                if (seen.find(detail::pack_edge_key(u, v, true)) == seen.end()) {
                    pool.emplace_back(u, v);
                }
            }
        }
        rng.shuffle_vec(pool);
        std::size_t need = static_cast<std::size_t>(m) - g.edges.size();
        for (std::size_t i = 0; i < need; ++i) {
            g.edges.emplace_back(pool[i].first + base, pool[i].second + base);
        }
    } else {
        while (static_cast<long long>(g.edges.size()) < m) {
            int u = rng.next_int(0, n - 1);
            int v = rng.next_int(0, n - 1);
            if (u == v) continue;
            if (u > v) std::swap(u, v);
            if (seen.insert(detail::pack_edge_key(u, v, true)).second) {
                g.edges.emplace_back(u + base, v + base);
            }
        }
    }

    return relabel ? g.relabeled(rng) : g;
}

inline Graph random_bipartite(
    int n1,
    int n2,
    long long m,
    bool connected = false,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n1 <= 0 || n2 <= 0 || m < 0) {
        throw std::invalid_argument("jngen::random_bipartite invalid dimensions or m");
    }
    int total = n1 + n2;
    if (connected && m < static_cast<long long>(total - 1)) {
        throw std::invalid_argument("jngen::random_bipartite connected requires m >= n1+n2-1");
    }
    long long max_edges = static_cast<long long>(n1) * static_cast<long long>(n2);
    if (m > max_edges) {
        throw std::invalid_argument("jngen::random_bipartite m > n1*n2");
    }

    int base = one_indexed ? 1 : 0;
    Graph g;
    g.n = total;
    g.directed = false;
    g.one_indexed = one_indexed;
    g.edges.reserve(static_cast<std::size_t>(m));
    std::unordered_set<std::uint64_t> seen;
    seen.reserve(static_cast<std::size_t>(m) * 2U + 1U);

    if (connected) {
        // Connect alternating sides into a spanning tree across the bipartition.
        int l = 0;
        int r = 0;
        while (l + 1 < n1 || r + 1 < n2) {
            int u = l;
            int v = n1 + r;
            if (seen.insert(detail::pack_edge_key(u, v, false)).second) {
                g.edges.emplace_back(u + base, v + base);
            }
            if (l + 1 < n1 && (r + 1 >= n2 || rng.next_bound(2ULL) == 0ULL)) {
                ++l;
            } else {
                ++r;
            }
        }
        // Connect the final vertex pair across sides if not yet linked.
        if (seen.insert(detail::pack_edge_key(l, n1 + r, false)).second) {
            g.edges.emplace_back(l + base, (n1 + r) + base);
        }
    }

    if (max_edges <= 2000000LL && m * 2LL > max_edges) {
        std::vector<std::pair<int, int>> pool;
        for (int u = 0; u < n1; ++u) {
            for (int v = n1; v < total; ++v) {
                if (seen.find(detail::pack_edge_key(u, v, false)) == seen.end()) {
                    pool.emplace_back(u, v);
                }
            }
        }
        rng.shuffle_vec(pool);
        std::size_t need = static_cast<std::size_t>(m) - g.edges.size();
        for (std::size_t i = 0; i < need; ++i) {
            g.edges.emplace_back(pool[i].first + base, pool[i].second + base);
        }
    } else {
        while (static_cast<long long>(g.edges.size()) < m) {
            int u = rng.next_int(0, n1 - 1);
            int v = n1 + rng.next_int(0, n2 - 1);
            if (seen.insert(detail::pack_edge_key(u, v, false)).second) {
                g.edges.emplace_back(u + base, v + base);
            }
        }
    }

    return relabel ? g.relabeled(rng) : g;
}

inline Graph grid_graph(
    int rows,
    int cols,
    bool directed = false,
    bool one_indexed = true,
    bool relabel = false,
    Random& rng = default_rng()
) {
    if (rows <= 0 || cols <= 0) {
        throw std::invalid_argument("jngen::grid_graph requires rows > 0 and cols > 0");
    }
    int base = one_indexed ? 1 : 0;
    Graph g;
    g.n = rows * cols;
    g.directed = directed;
    g.one_indexed = one_indexed;
    for (int r = 0; r < rows; ++r) {
        for (int c = 0; c < cols; ++c) {
            int u = r * cols + c;
            if (r + 1 < rows) g.edges.emplace_back(u + base, (u + cols) + base);
            if (c + 1 < cols) g.edges.emplace_back(u + base, (u + 1) + base);
        }
    }
    return relabel ? g.relabeled(rng) : g;
}

inline Graph cycle_graph(
    int n,
    bool directed = false,
    bool one_indexed = true,
    bool relabel = true,
    Random& rng = default_rng()
) {
    if (n <= 0) {
        throw std::invalid_argument("jngen::cycle_graph requires n > 0");
    }
    int base = one_indexed ? 1 : 0;
    Graph g;
    g.n = n;
    g.directed = directed;
    g.one_indexed = one_indexed;
    if (n >= 2) {
        for (int i = 0; i < n; ++i) {
            g.edges.emplace_back(i + base, ((i + 1) % n) + base);
        }
    }
    return relabel ? g.relabeled(rng) : g;
}

// Explicit topology-trap graph (self-loops, parallel multi-edges, isolated vertices).
inline Graph edge_case_graph(
    int n,
    long long m,
    bool include_self_loop = true,
    bool include_multi_edge = true,
    int isolated_nodes = 1,
    bool directed = false,
    bool one_indexed = true,
    Random& rng = default_rng()
) {
    if (n <= 0 || m < 0 || isolated_nodes < 0 || isolated_nodes >= n) {
        throw std::invalid_argument("jngen::edge_case_graph invalid parameters");
    }
    int active_n = n - isolated_nodes;
    int base = one_indexed ? 1 : 0;
    Graph g;
    g.n = n;
    g.directed = directed;
    g.one_indexed = one_indexed;
    g.edges.reserve(static_cast<std::size_t>(m));

    if (include_self_loop && static_cast<long long>(g.edges.size()) < m) {
        int u = rng.next_int(0, active_n - 1);
        g.edges.emplace_back(u + base, u + base);
    }
    if (include_multi_edge && active_n >= 2 && static_cast<long long>(g.edges.size()) + 2 <= m) {
        int u = rng.next_int(0, active_n - 1);
        int v = (u + 1) % active_n;
        g.edges.emplace_back(u + base, v + base);
        g.edges.emplace_back(u + base, v + base);
    }
    while (static_cast<long long>(g.edges.size()) < m) {
        int u = rng.next_int(0, active_n - 1);
        int v = rng.next_int(0, active_n - 1);
        g.edges.emplace_back(u + base, v + base);
    }
    return g.relabeled(rng);
}

// ---------------------------------------------------------------------------
// Strings
// ---------------------------------------------------------------------------

inline std::string random_string(
    int length,
    const std::string& alphabet = "abcdefghijklmnopqrstuvwxyz",
    Random& rng = default_rng()
) {
    if (length < 0 || alphabet.empty()) {
        throw std::invalid_argument("jngen::random_string invalid length or empty alphabet");
    }
    std::string s(static_cast<std::size_t>(length), '\0');
    for (int i = 0; i < length; ++i) {
        s[static_cast<std::size_t>(i)] =
            alphabet[static_cast<std::size_t>(rng.next_bound(alphabet.size()))];
    }
    return s;
}

inline std::string palindrome_string(
    int length,
    const std::string& alphabet = "ab",
    Random& rng = default_rng()
) {
    if (length < 0 || alphabet.empty()) {
        throw std::invalid_argument("jngen::palindrome_string invalid length or empty alphabet");
    }
    std::string s(static_cast<std::size_t>(length), '\0');
    int half = (length + 1) / 2;
    for (int i = 0; i < half; ++i) {
        char ch = alphabet[static_cast<std::size_t>(rng.next_bound(alphabet.size()))];
        s[static_cast<std::size_t>(i)] = ch;
        s[static_cast<std::size_t>(length - 1 - i)] = ch;
    }
    return s;
}

inline std::string fibonacci_string(int max_len, char c0 = 'a', char c1 = 'b') {
    if (max_len <= 0) {
        return "";
    }
    std::string a(1, c0);
    if (max_len == 1) return a;
    std::string b(1, c1);
    while (static_cast<int>(b.size()) < max_len) {
        std::string nxt = b + a;
        a = std::move(b);
        b = std::move(nxt);
    }
    b.resize(static_cast<std::size_t>(max_len));
    return b;
}

// ---------------------------------------------------------------------------
// Anti-Hash Generators
// ---------------------------------------------------------------------------

// Verified primes used in GNU libstdc++ __prime_list (bits/hashtable_policy.h).
inline const std::vector<long long>& gcc_unordered_map_primes() {
    static const std::vector<long long> primes = {
        10337LL,  11083LL,  11887LL,  12739LL,  13649LL,  14627LL,  15671LL,  16787LL,
        17987LL,  19273LL,  20663LL,  22147LL,  23741LL,  25447LL,  27277LL,  29231LL,
        31327LL,  33577LL,  35983LL,  38567LL,  41333LL,  44293LL,  47459LL,  50857LL,
        54493LL,  58391LL,  62563LL,  67033LL,  71821LL,  76949LL,  82457LL,  88337LL,
        94649LL,  101411LL, 107897LL, 108649LL, 116411LL, 124717LL, 126271LL, 133631LL,
        143177LL, 153407LL, 164363LL, 176089LL, 188677LL, 216617LL, 232103LL, 248683LL,
        266447LL, 305873LL, 327721LL,
    };
    return primes;
}

// Deterministic splitmix64 probe sequence (for testing fixed-seed custom hashes).
inline std::vector<std::uint64_t> splitmix64_probe(std::uint64_t seed, int count) {
    if (count < 0) {
        throw std::invalid_argument("jngen::splitmix64_probe requires count >= 0");
    }
    std::vector<std::uint64_t> out;
    out.reserve(static_cast<std::size_t>(count));
    for (int i = 0; i < count; ++i) {
        out.push_back(splitmix64(seed + static_cast<std::uint64_t>(i)));
    }
    return out;
}

// Finds `count` distinct keys whose splitmix64(key ^ seed) shares identical low `low_bits`.
inline std::vector<std::uint64_t> splitmix64_lowbit_collisions(
    std::uint64_t seed,
    int count,
    int low_bits = 12,
    std::uint64_t target_residue = 0ULL
) {
    if (count < 0 || low_bits <= 0 || low_bits > 24) {
        throw std::invalid_argument("jngen::splitmix64_lowbit_collisions invalid parameters");
    }
    std::uint64_t mask = (1ULL << low_bits) - 1ULL;
    std::uint64_t target = target_residue & mask;
    std::vector<std::uint64_t> keys;
    keys.reserve(static_cast<std::size_t>(count));
    for (std::uint64_t k = 0ULL; static_cast<int>(keys.size()) < count; ++k) {
        if ((splitmix64(k ^ seed) & mask) == target) {
            keys.push_back(k);
        }
    }
    return keys;
}

// Generates `n` distinct keys in [0, max_val] congruent to `offset (mod prime_bucket)`,
// forcing O(n^2) bucket chains in GNU std::unordered_map / std::unordered_set.
inline std::vector<long long> anti_unordered_map_sequence(
    int n,
    long long prime_bucket = 107897LL,
    long long max_val = 1000000000LL,
    long long offset = 1LL
) {
    if (n < 0 || prime_bucket <= 0 || max_val < 0) {
        throw std::invalid_argument("jngen::anti_unordered_map_sequence invalid args");
    }
    long long start = ((offset % prime_bucket) + prime_bucket) % prime_bucket;
    if (start > max_val) {
        throw std::invalid_argument("jngen::anti_unordered_map_sequence offset exceeds max_val");
    }
    long long slots = (max_val - start) / prime_bucket + 1LL;
    std::vector<long long> keys;
    keys.reserve(static_cast<std::size_t>(n));
    for (int i = 0; i < n; ++i) {
        long long idx = static_cast<long long>(i) % slots;
        keys.push_back(start + idx * prime_bucket);
    }
    return keys;
}

// Interleaves colliding sequences across two or more GCC bucket primes (e.g. 107897 & 126271)
// so both pre-rehash and post-rehash table sizes suffer linear-chain blowups.
inline std::vector<long long> anti_unordered_map_multi_prime(
    int n,
    long long max_val = 1000000000LL,
    const std::vector<long long>& prime_buckets = {107897LL, 126271LL}
) {
    if (n < 0 || max_val <= 0 || prime_buckets.empty()) {
        throw std::invalid_argument("jngen::anti_unordered_map_multi_prime invalid args");
    }
    std::vector<long long> usable_primes;
    usable_primes.reserve(prime_buckets.size());
    for (long long p : prime_buckets) {
        if (p <= 0) {
            throw std::invalid_argument("jngen::anti_unordered_map_multi_prime non-positive prime");
        }
        if (p <= max_val) {
            usable_primes.push_back(p);
        }
    }
    if (usable_primes.empty()) {
        throw std::invalid_argument(
            "jngen::anti_unordered_map_multi_prime no prime bucket <= max_val"
        );
    }
    std::vector<long long> keys;
    keys.reserve(static_cast<std::size_t>(n));
    if (n == 0) {
        return keys;
    }

    std::unordered_set<long long> seen;
    seen.reserve(static_cast<std::size_t>(n) * 2U + 1U);
    std::vector<long long> cursors(usable_primes.size(), 1LL);
    std::vector<long long> max_mults(usable_primes.size());
    for (std::size_t i = 0; i < usable_primes.size(); ++i) {
        max_mults[i] = max_val / usable_primes[i];
    }

    bool any_active = true;
    while (static_cast<int>(keys.size()) < n && any_active) {
        any_active = false;
        for (std::size_t idx = 0;
             idx < usable_primes.size() && static_cast<int>(keys.size()) < n;
             ++idx) {
            while (cursors[idx] <= max_mults[idx]) {
                long long v = cursors[idx] * usable_primes[idx];
                ++cursors[idx];
                if (seen.insert(v).second) {
                    keys.push_back(v);
                    any_active = true;
                    break;
                }
            }
        }
    }

    // Once distinct multiples <= max_val are exhausted, cycle deterministically
    // over collected colliding keys (bounded fallback matching anti_unordered_map_sequence).
    std::size_t distinct_count = keys.size();
    std::size_t cycle_idx = 0;
    while (static_cast<int>(keys.size()) < n) {
        keys.push_back(keys[cycle_idx % distinct_count]);
        ++cycle_idx;
    }
    return keys;
}

// Collides power-of-two masked open-addressing hash tables (key & ((1 << bits) - 1)).
inline std::vector<long long> anti_power_of_two_hash_sequence(
    int n,
    int bits = 16,
    long long max_val = 1000000000LL,
    long long offset = 1LL
) {
    if (n < 0 || bits <= 0 || bits > 30) {
        throw std::invalid_argument("jngen::anti_power_of_two_hash_sequence invalid args");
    }
    long long stride = 1LL << bits;
    return anti_unordered_map_sequence(n, stride, max_val, offset);
}

// Thue-Morse string of given length (character 0 -> c0, character 1 -> c1).
inline std::string thue_morse_string(int length, char c0 = 'a', char c1 = 'b') {
    if (length < 0) {
        throw std::invalid_argument("jngen::thue_morse_string requires length >= 0");
    }
    std::string s(static_cast<std::size_t>(length), c0);
    for (int i = 0; i < length; ++i) {
        // Parity of popcount(i) determines bit i of the Thue-Morse sequence.
        unsigned int v = static_cast<unsigned int>(i);
        int parity = 0;
        while (v != 0U) {
            parity ^= 1;
            v &= (v - 1U);
        }
        s[static_cast<std::size_t>(i)] = (parity == 0) ? c0 : c1;
    }
    return s;
}

// Returns (T_k, complement(T_k)) of length 2^exp_k. For exp_k >= 11, both strings
// produce identical uint64_t polynomial rolling hashes for EVERY odd base.
inline std::pair<std::string, std::string> thue_morse_collision_pair(
    int exp_k = 11,
    char c0 = 'a',
    char c1 = 'b'
) {
    if (exp_k < 1 || exp_k > 20) {
        throw std::invalid_argument("jngen::thue_morse_collision_pair requires 1 <= exp_k <= 20");
    }
    int len = 1 << exp_k;
    std::string a = thue_morse_string(len, c0, c1);
    std::string b = a;
    for (char& ch : b) {
        ch = (ch == c0) ? c1 : c0;
    }
    return {std::move(a), std::move(b)};
}

// Deterministic birthday-paradox collision finder for a single-modulus polynomial hash:
//   H(s) = sum_{i=0}^{len-1} (unsigned char)(s[i]) * base^i (mod mod)
// Works cleanly in milliseconds for typical 30-bit single moduli (1e9+7, 998244353).
inline std::pair<std::string, std::string> anti_single_mod_hash_pair(
    std::uint64_t mod = 1000000007ULL,
    std::uint64_t base = 131ULL,
    int length = 16,
    std::uint64_t seed = 1ULL,
    const std::string& alphabet = "abcdefghijklmnopqrstuvwxyz"
) {
    if (mod <= 1ULL || length < 4 || alphabet.size() < 2U) {
        throw std::invalid_argument("jngen::anti_single_mod_hash_pair invalid args");
    }
    auto eval_hash = [&](const std::string& s) -> std::uint64_t {
        std::uint64_t h = 0ULL;
        for (char ch : s) {
            h = (h * (base % mod) + static_cast<unsigned char>(ch)) % mod;
        }
        return h;
    };

    // Estimate birthday sample budget (~2.5 * sqrt(mod)), capped safely at 200000.
    std::size_t budget = 10000U;
    while (static_cast<std::uint64_t>(budget) * static_cast<std::uint64_t>(budget) < mod * 6ULL &&
           budget < 200000U) {
        budget += 5000U;
    }

    Random rng(seed);
    std::vector<std::pair<std::uint64_t, std::uint64_t>> table;
    table.reserve(budget);
    for (std::size_t idx = 0; idx < budget; ++idx) {
        std::uint64_t s_seed = rng.next_u64();
        Random str_rng(s_seed);
        std::string cand = random_string(length, alphabet, str_rng);
        table.emplace_back(eval_hash(cand), s_seed);
    }
    std::sort(table.begin(), table.end(),
              [](const auto& lhs, const auto& rhs) { return lhs.first < rhs.first; });

    for (std::size_t i = 1; i < table.size(); ++i) {
        if (table[i].first == table[i - 1].first) {
            Random r1(table[i - 1].second);
            Random r2(table[i].second);
            std::string s1 = random_string(length, alphabet, r1);
            std::string s2 = random_string(length, alphabet, r2);
            if (s1 != s2) {
                return {std::move(s1), std::move(s2)};
            }
        }
    }
    throw std::runtime_error("jngen::anti_single_mod_hash_pair did not find collision in budget");
}

}  // namespace jngen

#endif  // _AUTOCODE_JNGEN_H_
