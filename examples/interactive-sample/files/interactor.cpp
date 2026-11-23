#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    int secret = inf.readInt(1, 100, "S");
    int queries = 0;
    const int MAX_QUERIES = 20;

    while (true) {
        if (ouf.seekEof()) {
            quitf(_wa, "Premature EOF before final answer after %d queries", queries);
        }
        std::string op = ouf.readToken();
        if (op == "?") {
            ++queries;
            if (queries > MAX_QUERIES) {
                quitf(_wa, "Query limit exceeded: %d > %d", queries, MAX_QUERIES);
            }
            int x = ouf.readInt(1, 100, "x");
            int resp = (x < secret) ? -1 : (x > secret ? 1 : 0);
            std::cout << resp << '\n' << std::flush;
        } else if (op == "!") {
            int guess = ouf.readInt(1, 100, "s");
            if (!ouf.seekEof()) {
                quitf(_pe, "Unexpected extra output after final answer");
            }
            tout << "queries=" << queries << " guess=" << guess << "\n";
            if (guess == secret) {
                quitf(_ok, "Correct S=%d in %d queries", secret, queries);
            }
            quitf(_wa, "Wrong guess: expected %d, got %d", secret, guess);
        } else {
            quitf(_pe, "Unknown command token '%s'", op.c_str());
        }
    }
}
