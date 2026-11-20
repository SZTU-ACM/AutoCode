/**
 * AutoCode Run-Twice Communication Template (testlib.h)
 *
 * Execution contract (driven by CommunicationRunner.run_twice):
 * - Invocation: communicator <input_file> <pass_output_file> <prev_channel_or_ans_file>
 * - Environment variables:
 *   - AUTOCODE_COMM_STAGE  : 1-based stage index ("1" = channel/relay, "2" = final judge)
 *   - AUTOCODE_COMM_PASSES : total number of passes (default "2")
 *   - AUTOCODE_COMM_ROLE   : "channel" for stages < passes, "judge" for final stage
 *
 * Stage 1 (channel):
 * - inf: original test input
 * - ouf: Pass 1 contestant stdout (encoded message)
 * - Validate message format & communication budget, optionally apply channel noise,
 *   write Pass 2 input to std::cout, and exit with quitf(_ok, "bits_used=%d bytes_used=%d", ...).
 *
 * Stage 2 (judge):
 * - inf: original test input
 * - ouf: Pass 2 contestant stdout (decoded answer)
 * - ans: channel output produced by Stage 1
 * - Verify decoded answer and exit with quitf(_ok / _wa / _pe / quitp).
 */

#include <cstdlib>
#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    const char* stage_env = std::getenv("AUTOCODE_COMM_STAGE");
    int stage = stage_env ? std::atoi(stage_env) : 2;

    int n = inf.readInt();

    if (stage == 1) {
        // Read encoded binary message from Pass 1
        std::string msg = ouf.readToken();
        if (!ouf.seekEof()) {
            quitf(_dirt, "Unexpected extra output in Pass 1");
        }
        for (char c : msg) {
            if (c != '0' && c != '1') {
                quitf(_pe, "Encoded message must be a binary string of 0/1");
            }
        }
        int bits = static_cast<int>(msg.size());
        int bytes = (bits + 7) / 8;

        // Forward (optionally noisy) message to Pass 2 via stdout
        std::cout << msg << "\n";
        quitf(_ok, "channel forwarded bits_used=%d bytes_used=%d", bits, bytes);
    }

    // Stage 2: verify decoded answer from Pass 2
    int decoded = ouf.readInt();
    if (!ouf.seekEof()) {
        quitf(_dirt, "Unexpected extra output in Pass 2");
    }
    std::string channel_msg = ans.seekEof() ? "" : ans.readToken();
    int bits = static_cast<int>(channel_msg.size());
    int bytes = (bits + 7) / 8;

    if (decoded == n) {
        quitf(_ok, "OK bits_used=%d bytes_used=%d rounds=2", bits, bytes);
    }
    quitf(_wa, "Decoded %d, expected %d", decoded, n);
}
