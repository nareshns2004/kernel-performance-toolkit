#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    long iterations = argc > 1 ? strtol(argv[1], NULL, 10) : 100000000L;
    volatile uint64_t value = 1;
    for (long i = 0; i < iterations; i++) value = value * 1664525u + 1013904223u;
    printf("checksum=%llu\n", (unsigned long long)value);
    return 0;
}
