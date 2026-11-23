#include <cstddef>
#include <cstdlib>
#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    const char* stage_env = std::getenv("AUTOCODE_COMM_STAGE");
    int stage = stage_env ? std::atoi(stage_env) : 2;

    int mode = inf.readInt(0, 1, "mode");
    int x = inf.readInt(0, 15, "X");

    if (stage == 1) {
        std::string msg = ouf.readToken();
        if (!ouf.seekEof()) {
            quitf(_dirt, "Unexpected extra output in Pass 1");
        }
        if (msg.empty() || msg.size() > 12) {
            quitf(_wa, "Encoded bit length %d exceeds budget [1, 12]", static_cast<int>(msg.size()));
        }
        for (char c : msg) {
            if (c != '0' && c != '1') {
                quitf(_pe, "Encoded message must contain only '0' and '1'");
            }
        }
        int bits = static_cast<int>(msg.size());
        int bytes = (bits + 7) / 8;

        std::string noisy = msg;
        if (mode == 1) {
            std::size_t flip_pos = static_cast<std::size_t>((x * 7 + 3) % bits);
            noisy[flip_pos] = (noisy[flip_pos] == '0') ? '1' : '0';
        }
        std::cout << noisy << "\n";
        quitf(_ok, "channel forwarded bits_used=%d bytes_used=%d", bits, bytes);
    }

    int decoded = ouf.readInt(0, 15, "decoded");
    if (!ouf.seekEof()) {
        quitf(_dirt, "Unexpected extra output in Pass 2");
    }
    std::string channel_msg = ans.seekEof() ? "" : ans.readToken();
    int bits = static_cast<int>(channel_msg.size());
    int bytes = (bits + 7) / 8;

    if (decoded == x) {
        quitf(_ok, "OK bits_used=%d bytes_used=%d rounds=2", bits, bytes);
    }
    quitf(_wa, "Decoded %d, expected %d", decoded, x);
}
