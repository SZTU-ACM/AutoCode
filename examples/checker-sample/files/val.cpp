#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    inf.readInt(1, 1000000000, "n");
    inf.readEoln();
    inf.readEof();
    return 0;
}
