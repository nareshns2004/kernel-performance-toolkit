#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    size_t mib = argc > 1 ? strtoull(argv[1], NULL, 10) : 64;
    size_t bytes = mib * 1024 * 1024;
    char *buffer = malloc(bytes);
    if (!buffer) return 1;
    memset(buffer, 0x5a, bytes);
    printf("allocated=%zu MiB\n", mib);
    free(buffer);
    return 0;
}
